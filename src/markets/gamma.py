import json
import logging
from dataclasses import dataclass
from datetime import datetime

import aiohttp

from src.config import config

log = logging.getLogger(__name__)


@dataclass
class Market15m:
    """represents a 15-minute up/down market"""

    slug: str
    coin: str  # BTC, ETH, SOL, XRP
    title: str
    start_time: datetime
    end_time: datetime
    up_token_id: str
    down_token_id: str
    condition_id: str
    closed: bool = False

    @property
    def is_active(self) -> bool:
        now = datetime.utcnow()
        return self.start_time <= now <= self.end_time and not self.closed


class GammaClient:
    """client for polymarket gamma api (market data)"""

    def __init__(self):
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def fetch_15m_markets(self, limit: int = 50) -> list[Market15m]:
        """fetch active 15-minute markets for all coins"""
        url = f"{config.gamma_api}/events"
        params = {
            "tag_id": config.tag_15m,
            "closed": "false",
            "limit": limit,
            "order": "id",
            "ascending": "false",
        }

        try:
            session = await self._get_session()
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    log.error(f"gamma api error: {resp.status}")
                    return []

                events = await resp.json()
                markets = []

                for event in events:
                    market = self._parse_event(event)
                    if market:
                        markets.append(market)

                return markets
        except Exception as e:
            log.error(f"gamma fetch error: {e}")
            return []

    def _parse_event(self, event: dict) -> Market15m | None:
        """parse event json into Market15m"""
        try:
            slug = event.get("slug", "")

            # extract coin from slug (e.g. btc-updown-15m-xxx -> BTC)
            coin = None
            for c in config.coins:
                if slug.lower().startswith(c.lower()):
                    coin = c
                    break

            if not coin:
                return None

            # get market data
            markets = event.get("markets", [])
            if not markets:
                return None

            market_data = markets[0]
            token_ids = json.loads(market_data.get("clobTokenIds", "[]"))
            if len(token_ids) < 2:
                return None

            # parse times
            start_time_str = event.get("startTime")
            end_date_str = event.get("endDate")

            if not start_time_str:
                return None

            start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
            # 15m window, so end = start + 15 min
            from datetime import timedelta

            end_time = start_time + timedelta(minutes=15)

            return Market15m(
                slug=slug,
                coin=coin,
                title=event.get("title", ""),
                start_time=start_time.replace(tzinfo=None),
                end_time=end_time.replace(tzinfo=None),
                up_token_id=token_ids[0],
                down_token_id=token_ids[1],
                condition_id=market_data.get("conditionId", ""),
                closed=event.get("closed", False),
            )
        except Exception as e:
            log.error(f"error parsing event: {e}")
            return None

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
