from collections.abc import Generator
import re
import logging
from dataclasses import dataclass

from .langs import flags, Lang, LangId, id2lang, lang2ids

logger = logging.getLogger(__name__)


class Players(list[str]):
    def __init__(self, *args, **kwargs):
        ret = super().__init__(*args, **kwargs)
        self.swapPlayers()  # seem to exist on all pages but that could be false, to be sure check script_videos.js
        return ret

    def __call__(self, index: int) -> Generator[str]:
        yield from self

    def swapPlayers(self):
        if len(self) < 2:
            return
        self[0], self[1] = self[1], self[0]


class Languages(dict[LangId, Players]):
    def __init__(self, *args, **kargs):
        super().__init__(*args, **kargs)
        if not self:
            logger.warning("No player available for %s", self)

    @property
    def availables(self) -> dict[Lang, list[Players]]:
        availables: dict[Lang, list[Players]] = {}
        for lang_id, players in self.items():
            if availables.get(id2lang[lang_id]) is None:
                availables[id2lang[lang_id]] = []
            availables[id2lang[lang_id]].append(players)
        return availables

    def consume_player(
        self, prefer_languages: list[Lang], index: int
    ) -> Generator[str]:
        for prefer_language in prefer_languages:
            for players in self.availables.get(prefer_language, []):
                if players:
                    yield from players(index)

        for language in lang2ids:
            for players in self.availables.get(language, []):
                if players:
                    logger.warning(
                        "Language preference not respected. Using %s", language
                    )
                    yield from players(index)


@dataclass(frozen=True)
class Episode:
    languages: Languages
    serie_name: str = ""
    season_name: str = ""
    _name: str = ""
    index: int = 1

    @property
    def name(self):
        return self._name.strip()

    @property
    def fancy_name(self):
        return f"{self._name.lstrip()} " + " ".join(
            flags[lang] for lang in self.languages.availables if lang != "VOSTFR"
        )

    @property
    def season_number(self):
        match_season_number = re.search(r"\d+", self.season_name)
        return int(match_season_number.group(0)) if match_season_number else 0

    @property
    def long_name(self):
        return f"{self.season_name} - {self.name}"

    @property
    def short_name(self):
        return f"{self.serie_name} S{self.season_number:02}E{self.index:02}"

    def __str__(self):
        return self.fancy_name

    def consume_player(self, prefer_languages: list[Lang]) -> Generator[str]:
        yield from self.languages.consume_player(prefer_languages, self.index)

    def best(self, prefer_languages: list[Lang]) -> str | None:
        try:
            return next(self.consume_player(prefer_languages))
        except StopIteration:
            return None
