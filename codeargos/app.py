#!/usr/bin/env python3

import os
import sys
import getopt
import re
from codeargos.__version__ import __version__
import codeargos.Constants as Constants
from codeargos.webcrawler import WebCrawler
from datetime import tzinfo, timedelta, datetime, timezone
import logging

class CodeArgos:

    target_host=''

    def __init__(self, starting_url):
        self.target_host = starting_url
        self.visited = set()

    @classmethod
    def print_banner(cls):
        print("\n{0}===========================================".format(Constants.CYAN))
        print(" {0}CodeArgos{1} ({0}v{2}{1}) - Developed by @danaepp".format(Constants.WHITE, Constants.CYAN, __version__))
        print(" https://github.com/danaepp/CodeArgos")
        print("==========================================={0} \n".format(Constants.WHITE))

    @classmethod
    def display_usage(cls):
        print( 'codeargos.py -u example.com' )       

    @staticmethod
    def run(argv):
        CodeArgos.print_banner()

        try:
            opts, args = getopt.getopt(argv, "hu:t:d", ["help", "url=", "threads=", "debug"])
        except getopt.GetoptError:
            CodeArgos.display_usage()
            sys.exit(2)

        threads = os.cpu_count() * 5
        log_level = logging.FATAL

        for opt, arg in opts:
            if opt in ( "-h", "--help"):
                CodeArgos.display_usage()
                sys.exit()
            elif opt in ( "-u", "--url"):
                CodeArgos.target_host = arg
            elif opt in ( "-t", "--threads"):
                try:
                    threads = int(arg, base=10)
                except:
                    print( "Invalid thread count. Using defaults")
                    threads = os.cpu_count() * 5
            elif opt in ( "-d", "--debug" ):
                log_level = logging.DEBUG

        logging.basicConfig( filename="codeargos.log", level=log_level )

        code_blocks = 0
        scan_start = datetime.now(timezone.utc)
        print( "Attempting to scan {0} across {1} threads...".format(CodeArgos.target_host, threads))
        print( "Starting scan at {0} UTC".format(scan_start.strftime("%Y-%m-%d %H:%M")) )

        crawler = WebCrawler(CodeArgos.target_host, threads)
        crawler.start()

        scan_end = datetime.now(timezone.utc)
        elapsed_time = scan_end - scan_start
        page_cnt = crawler.processed()

        print( "Scan complete: found {0} code file/blocks on {1} pages in {2}".format( code_blocks, page_cnt, elapsed_time ) )