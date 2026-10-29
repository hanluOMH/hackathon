"""Persistent OAuth records; every value is encrypted before leaving the provider."""
import asyncio
import json
from datetime import datetime, timezone
from cryptography.fernet import Fernet


class MemoryStore:
    def __init__(self):
        self.data = {}
        self.lock = asyncio.Lock()

    async def get(self, key):
        async with self.lock:
            return self.data.get(key)

    async def put(self, key, value):
        async with self.lock:
            self.data[key] = value

    async def update(self, key, transform):
        async with self.lock:
            value = transform(self.data.get(key))
            if value is not None:
                self.data[key] = value
            return value

    async def consume(self, key):
        async with self.lock:
            return self.data.pop(key, None)


class FirestoreStore:
    def __init__(self, project=None, collection='hanluwork_oauth', database='hanluwork'):
        from google.cloud import firestore
        self.db = firestore.Client(project=project, database=database)
        self.collection = self.db.collection(collection)

    async def get(self, key):
        def read():
            doc = self.collection.document(key).get()
            return doc.to_dict() if doc.exists else None
        return await asyncio.to_thread(read)

    async def put(self, key, value):
        await asyncio.to_thread(self.collection.document(key).set, value)

    async def update(self, key, transform):
        from google.cloud import firestore
        def update():
            ref = self.collection.document(key)
            @firestore.transactional
            def transaction(tx):
                doc = ref.get(transaction=tx)
                value = transform(doc.to_dict() if doc.exists else None)
                if value is not None:
                    tx.set(ref, value)
                return value
            return transaction(self.db.transaction())
        return await asyncio.to_thread(update)

    async def consume(self, key):
        from google.cloud import firestore
        def consume():
            ref = self.collection.document(key)
            @firestore.transactional
            def transaction(tx):
                doc = ref.get(transaction=tx)
                if not doc.exists:
                    return None
                tx.delete(ref)
                return doc.to_dict()
            return transaction(self.db.transaction())
        return await asyncio.to_thread(consume)


class CipherStore:
    def __init__(self, store, key):
        self.store, self.cipher = store, Fernet(key.encode() if isinstance(key, str) else key)

    def encode(self, value):
        record = {'value': self.cipher.encrypt(json.dumps(value).encode()).decode()}
        if isinstance(value.get('expires_at'), (int, float)):
            record['expires_at'] = datetime.fromtimestamp(value['expires_at'], timezone.utc)
        return record

    def decode(self, record):
        if not record:
            return None
        # Read older ciphertext-only MemoryStore records as well.
        encrypted = record['value'] if isinstance(record, dict) else record
        return json.loads(self.cipher.decrypt(encrypted.encode()))

    async def put(self, key, value):
        await self.store.put(key, self.encode(value))

    async def get(self, key):
        return self.decode(await self.store.get(key))

    async def consume(self, key):
        return self.decode(await self.store.consume(key))

    async def update(self, key, transform):
        def apply(record):
            result = transform(self.decode(record))
            return self.encode(result) if result is not None else None
        return self.decode(await self.store.update(key, apply))
