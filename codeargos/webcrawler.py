#!/usr/bin/env python3
import re
import sys
import signal
from queue import Queue, Empty
from typing import List
from concurrent.futures import ThreadPoolExecutor, Future, ALL_COMPLETED
import concurrent
import logging
import time
import pprint
from codeargos.scraper import Scraper
from codeargos.datastore import DataStore
from codeargos.scrapedpage import ScrapedPage
from codeargos.displaydiff import DisplayDiff
from codeargos.webhook import WebHookType, WebHook
from codeargos.enums import CodeArgosPrintMode
from urllib.parse import urlparse

class WebCrawler:
    def __init__(self, seed_url, threads, stats, db_file_path, webhook_type, webhook_url, print_mode):
        self.seed_url = seed_url
        self.pool = ThreadPoolExecutor(max_workers=threads)        
        self.processed_urls = set([])
        self.queued_urls = Queue()
        self.queued_urls.put(self.seed_url)
        self.show_stats = stats
        self.scripts_found = 0
        self.diff_list = set([])
        self.print_mode = print_mode

        # Setup local sqlite database
        self.db_name = "unknown.db"
        if db_file_path is None:
            self.db_name = self.gen_db_name(seed_url)
        else:
            self.db_name = db_file_path
        self.data_store = DataStore(self.db_name)

        # Setup optional webhook for notifications
        if self.setup_webhook(webhook_url, webhook_type):
            self.webhook = WebHook(self.webhook_url, self.webhook_type)
        else:
            self.webhook = None    

        signal.signal(signal.SIGINT, self.dump_data)

    def __del__(self):       
        if( self.data_store ):
            try:
                self.data_store.close()
            except Exception as e:
                logging.exception(e)

    def gen_db_name(self, url):
        target = "unknown"
        try:
            parsed_url = urlparse(url) 
            if parsed_url.netloc:
                target = parsed_url.hostname
        except Exception as e:
            logging.exception(e)

        return target + ".db" 

    def setup_webhook(self, url, hooktype):

        webhook_enabled = False

        if hooktype and url:
            msg = ""
            self.webhook_url = url
            if hooktype == "slack":
                self.webhook_type = WebHookType.SLACK
                msg = "Configured SLACK webhook to {0}".format(self.webhook_url)
            elif hooktype == "teams":
                self.webhook_type = WebHookType.TEAMS
                msg = "Configured TEAMS webhook to {0}".format(self.webhook_url)
            elif hooktype == "discord":
                self.webhook_type = WebHookType.DISCORD
                msg = "Configured DISCORD webhook to {0}".format(self.webhook_url)
            elif hooktype == "generic":
                self.webhook_type = WebHookType.GENERIC
                msg = "Configured GENERIC webhook to {0}".format(self.webhook_url)
            else:
                self.webhook_type = WebHookType.NONE
                self.webhook_url = ""
                msg = "Couldn't properly parse out the webhook settings. Ignoring and will not send webhook notifications."
            
            logging.debug( msg ) 

            if self.webhook_url:
                webhook_enabled = True 
        else:
            self.webhook_type = WebHookType.NONE
            self.webhook_url = ""
            logging.debug( "No webhooks configured.")
            webhook_enabled = False

        return webhook_enabled

    def dump_data(self, signal, frame):
        choice = input( "\nEarly abort detected. Dump data already collected? (to processed.txt): [y/N] ")
        choice = choice.lower()
        if choice == 'y':
            with open('processed.txt', 'w') as f:
                for item in self.processed_urls:
                    f.write("%s\n" % item)
        sys.exit()

    def process_scraper_results(self, future):
        # get the items of interest from the future object
        internal_urls = future._result[0]
        url = future._result[1]
        sig = future._result[2]
        new_content = future._result[3]
        raw_content = future._result[4]
        diff_content = future._result[5]
        
        # There are occassions when an unknown media type gets through and 
        # can't be properly hashed, which leaves sig empty. Instead of b0rking, 
        # let's just let it go and move on.
        if new_content and sig:
            page = ScrapedPage(url, sig, raw_content)
            self.data_store.add_page(page)
            if diff_content:
                diff_id = self.data_store.add_diff(url,diff_content)
                self.diff_list.add(diff_id)
                self.notify_webhook(url, diff_id)                

        # also add scraped links to queue if they
        # aren't already queued or already processed
        for link_url in internal_urls:
            # We have to account for not just internal pages, but external scripts foreign to 
            # the target app. ie: jQuery, Angular etc
            if link_url.startswith(self.seed_url) or link_url.lower().endswith(".js"):
                if link_url not in self.queued_urls.queue and link_url not in self.processed_urls:
                    self.queued_urls.put(link_url)
    
    @property
    def processed(self):
        return len(self.processed_urls)

    def dump_pages(self):
        self.data_store.dump_pages()

    def notify_webhook(self, url, diff_id):
        if self.webhook:
            message = "Changes detected on {0}. Review in {1} [#diff: {2}]".format(url, self.db_name, diff_id)
            self.webhook.notify(message, url)

    def start(self):
        LOG_EVERY_N = 500
        i = 0

        jobs = []
        
        while True:
            try:                
                # get a url from the queue
                target_url = self.queued_urls.get(timeout=15)

                # check that the url hasn't already been processed
                if target_url not in self.processed_urls:
                    # add url to the processed list
                    self.processed_urls.add(target_url)

                    logging.debug(f'[URL] {target_url}')

                    # Check the datastore to see if we have a sig for this page
                    scraped_page = self.data_store.get_page(target_url) 

                    job = self.pool.submit(Scraper(target_url, scraped_page).scrape)
                    job.add_done_callback(self.process_scraper_results)
                    jobs.append(job)

                if self.show_stats:
                    if i % LOG_EVERY_N == 0:
                        print("Processed: {0:<8} | Queue: {1:<8} | Scheduled Jobs: {2:<8}".format(
                            len(self.processed_urls), 
                            self.queued_urls.qsize(),
                            self.pool._work_queue.qsize()))  

                i=i+1
            except Empty:
                logging.debug("All queues and jobs complete.")                
                
                # We need to wait until all child threads/jobs have completed before we can dump
                # the diffs. If we don't wait, we may miss a few children still being processed and
                # could cause a runtime exception due to the diff_list size changing.
                concurrent.futures.wait(jobs, timeout=None, return_when=ALL_COMPLETED)

                if len(self.diff_list) > 0:
                    diff_viewer = DisplayDiff(self.db_name)
                    
                    try:
                        if self.print_mode == CodeArgosPrintMode.DIFF or self.print_mode == CodeArgosPrintMode.BOTH:
                            for diff_id in self.diff_list.copy():
                                diff_viewer.show(diff_id)
                    except Exception as e:
                        logging.exception(e)
                    finally:
                        if self.print_mode == CodeArgosPrintMode.ID or self.print_mode == CodeArgosPrintMode.BOTH:
                            print( "diffs: {0}".format(self.diff_list))
                return
            except Exception as e:
                logging.exception(e)
                continue
                
