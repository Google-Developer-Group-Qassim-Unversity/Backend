# this script is for testing.
# it doesn't get used by the server.

# this script takes a google sheets link from stdin 
# and prints the sheet as a python dict. 

import sys
import urllib.request
import io
import csv
from pprint import pprint


def main():
    sheet_link = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRcmCPGpWxuj9y8LEjeKPhBW77qRvyWs3g5wAH5eA5weEPASXj-FvhLUwa_CNW5ZX9D6c3qyOk5bej0/pub?gid=1781104695&single=true&output=csv"
    response = urllib.request.urlopen(sheet_link)
    csv_data = response.read().decode("utf-8")
    csv_file = io.StringIO(csv_data)

    for row in csv.DictReader(csv_file):
        print(f"'{row.get('phone number')}',")


if __name__ == "__main__":
    main()
