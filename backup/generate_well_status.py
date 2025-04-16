from LabMind import nosql_service, KnowledgeObject


rows = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
columns = [str(i) for i in range(1, 13)]

for row in rows:
    for col in columns:
        well = f"{row}{col}"
        metadata = {
            "project": "OT2",
            "collection": "wells",
            "well": well,
            "status": "empty",
            "unique_fields": ["well"]
        }
        well = KnowledgeObject(metadata=metadata, nosql_service=nosql_service, embedding=False)
        well.store()
