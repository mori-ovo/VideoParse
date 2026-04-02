from abc import ABC, abstractmethod


class VideoPlatformAdapter(ABC):
    platform_name: str

    @abstractmethod
    def match(self, url: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def parse(self, url: str) -> dict[str, object]:
        raise NotImplementedError

    @abstractmethod
    async def download(self, metadata: dict[str, object]) -> dict[str, object]:
        raise NotImplementedError

