from azure.storage.blob import BlobServiceClient
import pathlib

CONNECTION_STRING = "DefaultEndpointsProtocol=https;AccountName=<STORAGE_ACCOUNT>;AccountKey=<STORAGE_KEY>;EndpointSuffix=core.windows.net"
CONTAINER = "container"
PREFIX = "internal/sandeep-alur/"
DEST = pathlib.Path(r"C:\Users\Yash\Downloads\sandeep-alur")

client = BlobServiceClient.from_connection_string(CONNECTION_STRING)
container_client = client.get_container_client(CONTAINER)

blobs = [b for b in container_client.list_blobs(name_starts_with=PREFIX)]
print(f"Found {len(blobs)} files. Downloading...")

for blob in blobs:
    dest_path = DEST / blob.name
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dest_path, "wb") as f:
        data = container_client.get_blob_client(blob.name).download_blob()
        data.readinto(f)
    print(f"  Downloaded: {blob.name}")

print("Done!")
