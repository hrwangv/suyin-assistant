from qdrant_client import QdrantClient

client = QdrantClient(
    host="175.27.142.33",
    port=6333
)



client.create_collection(collection_name="test",)

from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, Document

client = QdrantClient(
    url="https://xyz-example.qdrant.io:6333",
    api_key="<paste-your-api-key-here>",
    # IMPORTANT
    # If not enabled, inference will be performed locally
    cloud_inference=True,
)

points = [
    PointStruct(
        id=1,
        payload={"topic": "cooking", "type": "dessert"},
        vector=Document(
            text="Recipe for baking chocolate chip cookies",
            model="<the-model-to-use>"
        )
    )
]

