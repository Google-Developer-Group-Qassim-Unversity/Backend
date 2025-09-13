import sys
import json
import urllib.request
import os

def main():
    json_text = sys.stdin.read()
    
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as e:
        print("Invalid JSON:", e)
        return

    os.makedirs("downloads", exist_ok=True)

    for name, url in data.items():
        filename = name.replace(" ", "_") + ".csv"
        filepath = os.path.join("downloads", filename)

        try:
            print(f"Downloading {url} -> {filepath}")
            urllib.request.urlretrieve(url, filepath)
        except Exception as e:
            print(f"Failed to download {url}: {e}")

if __name__ == "__main__":
    main()
