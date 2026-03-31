from app.models.schemas import IntentSpec
import json

fields = list(IntentSpec.model_fields.keys())
print(fields)