"""GitHub issue creation, with no redirects and no automatic POST retries."""
import json
import time
import urllib.error
import urllib.request


class GitHubError(Exception):
    def __init__(self, state, retry_after=300):
        super().__init__(state)
        self.state = state
        self.retry_after = retry_after


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHub:
    def __init__(self, token, repository):
        self.token = token
        self.api = "https://api.github.com/repos/" + repository + "/issues"
        self.web = "https://github.com/" + repository + "/issues/"

    def request(self, payload):
        request = urllib.request.Request(
            self.api,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "User-Agent": "CoA-Bug-Report/1",
                "X-GitHub-Api-Version": "2026-03-10",
            },
        )
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
                if response.status != 201:
                    raise GitHubError("uncertain")
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise GitHubError("uncertain")
                return json.loads(raw)
        except urllib.error.HTTPError as error:
            limited = error.code == 429 or (error.code == 403 and (
                error.headers.get("Retry-After") or error.headers.get("X-RateLimit-Remaining") == "0"
            ))
            if limited:
                delay = 300
                try:
                    if error.headers.get("Retry-After"):
                        delay = int(error.headers["Retry-After"])
                    elif error.headers.get("X-RateLimit-Reset"):
                        delay = int(error.headers["X-RateLimit-Reset"]) - int(time.time()) + 5
                except ValueError:
                    pass
                raise GitHubError("queued", max(60, min(delay, 86400))) from None
            if error.code >= 500 or error.code == 408:
                raise GitHubError("uncertain") from None
            if error.code in (400, 422):
                raise GitHubError("failed", 0) from None
            # Authentication, repository permissions, moved repos, and other rejections.
            raise GitHubError("blocked") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise GitHubError("uncertain") from None

    def number(self, issue):
        if not isinstance(issue, dict):
            raise GitHubError("uncertain")
        number = issue.get("number")
        if (type(number) is not int or not 0 < number <= 4294967295
                or issue.get("html_url") != self.web + str(number)
                or "pull_request" in issue):
            raise GitHubError("uncertain")
        return number

    def create(self, title, body):
        return self.number(self.request({"title": title, "body": body}))
