import sys
import requests
import json


def main():
    if len(sys.argv) < 2:
        print('Usage: python cli.py "你的自然语言观点"')
        return

    user_text = sys.argv[1]

    resp = requests.post(
        "http://127.0.0.1:8005/advisor/run",
        json={"text": user_text},
        timeout=120
    )

    print(json.dumps(resp.json(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()