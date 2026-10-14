"""The shape of an extracted post. Shared by the API route (extract.py) and the
in-chat route (manual.py) so geocode/export work the same either way."""
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

Category = Literal[
    "restaurant", "cafe", "bar", "bakery", "shop", "market", "hotel",
    "attraction", "museum", "viewpoint", "beach", "nature", "park",
    "neighborhood", "city", "other",
]


class Place(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(description="The place's proper name as it would appear on a map, e.g. 'Café Lumen'. For a whole town or area, its name.")
    category: Category
    address: Optional[str] = Field(description="Street address if stated or clearly visible, else null.")
    neighborhood: Optional[str]
    city: Optional[str]
    region: Optional[str] = Field(description="State, province or island, if known.")
    country: Optional[str]
    details: str = Field(description="Useful specifics from the post: what to order or buy, prices, hours, booking tips, why it's recommended. Empty string if none.")
    confidence: Literal["high", "medium", "low"] = Field(description="How sure you are this exact place is what the post is about.")


class PostExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(description="One sentence on what the post is about.")
    places: List[Place]
