"""Current and delisted issues for historical KOSPI backfills."""


def kospi_history_sources(fdr, start, end):
    active = fdr.StockListing("KOSPI")
    delisted = fdr.StockListing("KRX-DELISTING", start, end)
    # The provider can return a schema-less frame on a cache/download error.
    # Do not silently fall back to today's constituents.
    kospi = delisted[delisted["Market"].isin(["KOSPI", "유가증권", "유가증권시장"])]
    sources = {str(code): str(code) for code in active["Code"]}
    for symbol in kospi["Symbol"]:
        code = str(symbol)
        if code.startswith("KR") and len(code) == 12:
            code = code[3:9]
        sources.setdefault(code, f"KRX-DELISTING:{code}")
    return sources
