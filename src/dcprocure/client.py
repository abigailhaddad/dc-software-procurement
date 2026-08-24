"""Client for the DC Contracts and Procurement Transparency Portal API.

The portal at https://contracts.ocp.dc.gov is an Angular app backed by an
undocumented but public JSON API. Nothing here requires authentication.

Quirks worth knowing, all verified against the live API:

* The API rejects requests without a browser-ish ``User-Agent`` (403).
* Search endpoints take POST with a ``{"FilterBy": [...], "OrderBy": []}`` body
  and return the *entire* result set in one response. There is no pagination.
* The server echoes the filters it actually applied in the ``filters`` key.
  Filters it does not understand are dropped **silently** -- see
  :meth:`DCProcurementClient.search`, which raises rather than let that pass.
* ``FiscalYear`` is accepted by the search form but never applied server-side
  for contracts. Filter on dates locally instead.
* ``CommodityCode`` takes a three-digit NIGP class; result rows carry the full
  seven-digit code, so the class is the first three characters.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

BASE_URL = "https://contracts.ocp.dc.gov"

# The API 403s a default python-httpx User-Agent.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Search modules exposed by the portal.
MODULES = ("contracts", "purchase", "payments", "solicitations", "forecast")

# The solicitations module keys off a numeric enum rather than a string, and
# rejects a search that omits it. "Open" means currently accepting responses;
# "Archived" is everything historical, back to the late 1990s.
SOLICITATION_OPEN = 0
SOLICITATION_ARCHIVED = 1


class PortalError(RuntimeError):
    """The portal returned an error, or quietly ignored what we asked for."""


@dataclass(frozen=True)
class Filter:
    """One search criterion, matching the shape the portal's own UI sends."""

    name: str
    value: Any

    def as_payload(self) -> dict[str, Any]:
        return {"name": self.name, "value": self.value}


@dataclass
class DCProcurementClient:
    """Thin, synchronous wrapper over the transparency portal API.

    Args:
        timeout: Per-request timeout in seconds. Payment searches return
            hundreds of thousands of rows and genuinely need a long timeout.
        max_retries: Retries for transient network/5xx failures.
        pause: Seconds to sleep between requests, to stay a polite client.
    """

    timeout: float = 300.0
    max_retries: int = 3
    pause: float = 0.1
    _client: httpx.Client = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._client = httpx.Client(
            base_url=BASE_URL,
            timeout=self.timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=True,
        )

    def __enter__(self) -> "DCProcurementClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- low level ---------------------------------------------------------

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = self._client.request(method, url, **kwargs)
                if response.status_code >= 500:
                    raise PortalError(f"{response.status_code} from {url}")
                response.raise_for_status()
                time.sleep(self.pause)
                return response
            except (httpx.TransportError, PortalError, httpx.HTTPStatusError) as exc:
                last = exc
                time.sleep(2**attempt)
        raise PortalError(f"{method} {url} failed after {self.max_retries} tries: {last}")

    # -- lookups -----------------------------------------------------------

    def lookup(self, module: str, name: str) -> Any:
        """Fetch a reference list, e.g. ``lookup("contracts", "commodities")``.

        Valid names include ``agencies``, ``commodities``, ``suppliers``,
        ``titles`` and ``contractingofficers``, though not every module
        supports every one.
        """
        return self._request("GET", f"/api/{module}/{name}").json()

    def commodities(self) -> list[dict[str, str]]:
        """The 266 three-digit NIGP commodity classes, with descriptions."""
        return self.lookup("contracts", "commodities")

    def agencies(self) -> list[dict[str, Any]]:
        """Agency clusters, each containing its member agencies."""
        return self.lookup("contracts", "agencies")

    # -- search ------------------------------------------------------------

    def search(
        self,
        module: str,
        filters: list[Filter],
        *,
        require_applied: bool = True,
    ) -> list[dict[str, Any]]:
        """Run a search and return all result rows.

        The portal drops filters it does not recognize without complaint, which
        makes it easy to believe you queried a narrow slice when you pulled the
        whole universe. By default we compare the filters the server echoed
        against what we sent and raise if any went missing.

        Args:
            module: One of :data:`MODULES`.
            filters: Criteria to apply. At least one is required by the API.
            require_applied: Raise if the server silently dropped a filter.

        Returns:
            All matching rows. The API does not paginate.

        Raises:
            PortalError: On an API error, or a silently dropped filter.
        """
        if module not in MODULES:
            raise ValueError(f"unknown module {module!r}; expected one of {MODULES}")
        if not filters:
            raise ValueError("the portal requires at least one filter")

        payload = {"FilterBy": [f.as_payload() for f in filters], "OrderBy": []}
        body = self._request("POST", f"/api/{module}/search", json=payload).json()

        if body.get("errorMessage"):
            raise PortalError(f"{module} search: {body['errorMessage']}")

        if require_applied:
            self._assert_filters_applied(module, filters, body.get("filters") or [])

        return body.get("results") or []

    @staticmethod
    def _assert_filters_applied(
        module: str,
        sent: list[Filter],
        echoed: list[dict[str, Any]],
    ) -> None:
        """Raise if the server ignored a filter we sent.

        The echoed filters are labelled for display ("NIGP Code") rather than by
        the name we sent ("CommodityCode"), so we compare on values, which is
        the part that actually determines the slice.
        """
        # These select which query to run or drive the UI; they do not narrow
        # the result set, and the server never echoes them.
        structural = {"IsExpanded", "DatesOption", "Type"}
        wanted = {str(f.value) for f in sent if f.name not in structural}
        applied = {str(f.get("value")) for f in echoed}
        missing = wanted - applied
        if missing:
            raise PortalError(
                f"{module} search silently ignored filter value(s) {sorted(missing)}; "
                f"the server applied only {sorted(applied)}. "
                "Results would cover a wider slice than requested."
            )

    # -- detail and documents ---------------------------------------------

    def contract_details(self, contract_id: str) -> dict[str, Any]:
        """Detail record for one contract option period.

        This is the only place ``contractType`` (Firm Fixed Price, Time and
        Materials, IDIQ, ...) and the list of attached PDFs are exposed. It
        takes the opaque ``id`` from a search row, not the contract number.
        """
        response = self._request(
            "GET", "/api/contracts/details", params={"id": contract_id}
        )
        return response.json()

    def attachment(self, document_id: str) -> bytes:
        """Download one contract PDF by document id.

        Note the path has no ``/api`` prefix; that is the portal's own routing,
        not a mistake.
        """
        return self._request("GET", f"/contracts/attachments/{document_id}").content

    # -- solicitations -----------------------------------------------------

    def open_solicitations(self) -> list[dict[str, Any]]:
        """Solicitations currently accepting responses.

        ``Type`` alone is rejected by the server, so ``Status`` goes with it.
        """
        return self.search(
            "solicitations",
            [
                Filter("Type", SOLICITATION_OPEN),
                Filter("Status", "Open"),
            ],
        )

    def archived_solicitations(self, fiscal_year: str) -> list[dict[str, Any]]:
        """Closed solicitations for one fiscal year, e.g. ``"FY2025"``.

        The archive holds roughly 17,800 solicitations in total. Pull it a year
        at a time: ``ArchivedFiscalYear`` is applied server-side, while the
        ``Keyword`` filter on this module is silently ignored, so any
        subject-matter narrowing has to happen locally on the title.
        """
        return self.search(
            "solicitations",
            [
                Filter("Type", SOLICITATION_ARCHIVED),
                Filter("ArchivedFiscalYear", fiscal_year),
            ],
        )
