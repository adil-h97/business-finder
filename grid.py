"""Geographic rectangles and adaptive subdivision.

Places API text search caps at 20 results per request and 60 per query, so a
metro has to be covered by many small searches. A uniform grid wastes requests
on empty cells, and requests are the scarce resource (1,000/month on the free
Enterprise tier). So: start coarse, and only split a cell that came back full.
"""

from dataclasses import dataclass

KM_PER_DEG_LAT = 110.574


@dataclass(frozen=True)
class Rect:
    south: float
    west: float
    north: float
    east: float

    @property
    def center(self) -> tuple[float, float]:
        return ((self.south + self.north) / 2, (self.west + self.east) / 2)

    def height_km(self) -> float:
        return (self.north - self.south) * KM_PER_DEG_LAT

    def width_km(self) -> float:
        import math

        mid_lat = (self.south + self.north) / 2
        km_per_deg_lng = 111.320 * math.cos(math.radians(mid_lat))
        return (self.east - self.west) * km_per_deg_lng

    def quarters(self) -> list["Rect"]:
        mid_lat, mid_lng = self.center
        return [
            Rect(self.south, self.west, mid_lat, mid_lng),
            Rect(self.south, mid_lng, mid_lat, self.east),
            Rect(mid_lat, self.west, self.north, mid_lng),
            Rect(mid_lat, mid_lng, self.north, self.east),
        ]

    def to_api(self) -> dict:
        return {
            "low": {"latitude": self.south, "longitude": self.west},
            "high": {"latitude": self.north, "longitude": self.east},
        }

    def __str__(self) -> str:
        return f"{self.width_km():.1f}x{self.height_km():.1f}km @ {self.center[0]:.3f},{self.center[1]:.3f}"
