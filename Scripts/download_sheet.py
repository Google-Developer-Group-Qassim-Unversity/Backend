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
    sheet_link = sys.stdin.read()
    response = urllib.request.urlopen(sheet_link)
    csv_data = response.read().decode("utf-8")
    csv_file = io.StringIO(csv_data)

    data = [row for row in csv.DictReader(csv_file)]
    pprint(data, indent=4)


if __name__ == "__main__":
    main()
