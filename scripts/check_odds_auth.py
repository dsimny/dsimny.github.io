"""Read-only, quota-free Odds API authentication check; never log credentials."""
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    key = os.environ.get("ODDS_API_KEY", "")
    if not key:
        print("Odds authentication: MISSING_KEY")
        return 1
    url = "https://api.the-odds-api.com/v4/sports/?" + urlencode({"apiKey": key})
    try:
        with build_opener(NoRedirect()).open(Request(url), timeout=20) as response:
            data = json.load(response)
            if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
                print("Odds authentication: INVALID_RESPONSE")
                return 1
            print("Odds authentication: PASS (HTTP 200)")
            print("Available sports:", len(data))
            for header in ("x-requests-remaining", "x-requests-used", "x-requests-last"):
                value = response.headers.get(header, "")
                if value.isdecimal():
                    print(header + ":", int(value))
            return 0
    except HTTPError as exc:
        print("Odds authentication: HTTP_FAILURE", int(exc.code))
    except (URLError, TimeoutError, OSError):
        print("Odds authentication: TRANSPORT_FAILURE")
    except Exception:
        # Exceptions may embed the credential-bearing URL. Never print them.
        print("Odds authentication: INVALID_RESPONSE")
    return 1


if __name__ == "__main__":
    sys.exit(main())
