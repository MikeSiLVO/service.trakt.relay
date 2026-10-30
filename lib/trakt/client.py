import http.client
import logging
import time
from json import dumps, loads
from typing import Any, Callable, Dict, Optional
import urllib.error
import urllib.parse
import urllib.request


logger = logging.getLogger(__name__)


class TraktClient:
    """HTTP client for the Trakt API, waiting out rate limits once, with a hook for error codes."""
    api_url = "https://api.trakt.tv"
    max_retry_after = 60
    on_error: Optional[Callable[[int], None]] = None
    monitor: Any = None

    def __init__(self, client_id: str, user_agent: str, proxy_url: Optional[str] = None) -> None:
        self.client_id = client_id
        self.user_agent = user_agent
        self.proxy_url = proxy_url

    def request(
        self,
        method: str,
        path: str,
        body: Optional[Dict] = None,
        authorization: Optional[Dict] = None,
        timeout: int = 30,
        retry: bool = True,
        include_headers: bool = False,
        include_error_code: bool = False,
    ) -> Any:
        """Request reply or None, paired with status if asked or headers if OK; 401 may raise."""
        url = self.api_url + path
        data = dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "Content-Type": "application/json",
            "trakt-api-version": "2",
            "trakt-api-key": self.client_id,
            "User-Agent": self.user_agent,
        }
        if authorization and authorization.get("access_token"):
            headers["Authorization"] = "Bearer %s" % authorization["access_token"]

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        opener = urllib.request.build_opener()
        if self.proxy_url:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler(
                    {"http": self.proxy_url, "https": self.proxy_url}
                )
            )

        try:
            with opener.open(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                data = loads(raw) if raw else None
                if include_error_code:
                    return data, None
                if include_headers:
                    return data, response.headers
                return data
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and retry:
                retry_after = self._retry_after(exc)
                if retry_after is not None:
                    logger.debug(
                        "Trakt rate limit reached: retrying %s %s after %s seconds"
                        % (method, path, retry_after)
                    )
                    if not self._wait(retry_after):
                        return self.request(
                            method,
                            path,
                            body,
                            authorization=authorization,
                            timeout=timeout,
                            retry=False,
                            include_headers=include_headers,
                            include_error_code=include_error_code,
                        )
            if self.on_error is not None:
                self.on_error(exc.code)
            if exc.code == 401 and authorization and authorization.get("refresh_token"):
                raise
            if include_error_code:
                return None, exc.code
            logger.debug("Trakt request failed: %s %s -> %s" % (method, path, exc.code))
            return None
        except (OSError, http.client.HTTPException, ValueError) as exc:
            logger.debug("Trakt request failed: %s %s -> %s" % (method, path, exc))
            if include_error_code:
                return None, None
            return None

    def _wait(self, seconds: int) -> bool:
        """Wait out a delay, True when Kodi starts exiting first."""
        # waitForAbort(0) waits until Kodi exits
        if self.monitor is None or seconds <= 0:
            time.sleep(max(seconds, 0))
            return False
        return self.monitor.waitForAbort(seconds)

    def _retry_after(self, exc: urllib.error.HTTPError) -> Optional[int]:
        """Read a 429's Retry-After seconds, or None when absent, negative or over the cap."""
        try:
            retry_after = int(exc.headers.get("Retry-After") or "")
        except (TypeError, ValueError):
            return None

        if retry_after < 0 or retry_after > self.max_retry_after:
            return None
        return retry_after

    def build_path(self, path: str, params: Optional[Dict] = None) -> str:
        """Build a path with every non-None value appended as a query string."""
        if not params:
            return path
        clean_params = {k: v for k, v in params.items() if v is not None}
        if not clean_params:
            return path
        separator = "&" if "?" in path else "?"
        return "%s%s%s" % (path, separator, urllib.parse.urlencode(clean_params))
