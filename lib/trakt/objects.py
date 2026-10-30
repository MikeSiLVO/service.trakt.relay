from typing import Any, Dict, List, Optional


class TraktObject:
    """Trakt item with attribute access to its fields, ids as (type, id) pairs and episodes."""
    def __init__(
        self,
        data: Optional[Dict] = None,
        show: Optional["TraktObject"] = None,
        keys: Optional[List] = None,
    ) -> None:
        self._data = data or {}
        self._keys = keys
        self.show = show
        self.pk = (self._data.get("season"), self._data.get("number"))
        self.episodes = self._build_episodes()

    def _build_episodes(self) -> Dict:
        """Build every season's episodes by number, each linked back to this show."""
        episodes = {}
        for season in self._data.get("seasons", []) or []:
            for episode in season.get("episodes", []) or []:
                item = dict(episode)
                item.setdefault("season", season.get("number"))
                episodes[item.get("number")] = TraktObject(item, show=self)
        return episodes

    def __getattr__(self, name: str) -> Any:
        try:
            return self._data[name]
        except KeyError:
            raise AttributeError(name) from None

    @property
    def keys(self) -> List:
        """Ids as (type, id) pairs, unless fixed at construction."""
        if self._keys is not None:
            return self._keys
        return list((self._data.get("ids") or {}).items())

    def to_dict(self) -> Dict:
        """Copy of the item's fields."""
        return dict(self._data)

    def __repr__(self) -> str:
        return repr(self._data)


class TraktSeason:
    """Trakt season with its episodes by number."""
    def __init__(self, data: Dict) -> None:
        self._data = data
        self.number = data.get("number")
        self.episodes = {}
        for episode in data.get("episodes", []) or []:
            item = dict(episode)
            item.setdefault("season", self.number)
            self.episodes[item.get("number")] = TraktObject(
                item, keys=[(self.number, item.get("number"))]
            )

    def __repr__(self) -> str:
        return repr(self._data)
