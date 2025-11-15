from turtle import delay
from . import views
from graphs.models import APICache
from django.http import JsonResponse
from django.utils import timezone
from datetime import time, timedelta
import threading
import json
import hashlib
from django.http import StreamingHttpResponse
import time  # standard Python time module, not datetime.time


TTL = timedelta(hours=1)  # Cache expiration time

def get_request_hash(request, slum_id):
    """
    Generate a unique hash from slum_id and request GET parameters
    """
    params = {"slum_id": slum_id, **request.GET.dict()}
    params_string = json.dumps(params, sort_keys=True)
    return hashlib.sha256(params_string.encode("utf-8")).hexdigest()


def compute_and_update_cache(request, slum_id, req_hash):
    """
    Compute fresh response by calling original view
    """
    # Call the original view to get JsonResponse
    response = views.get_component(request, slum_id)

    # Convert JsonResponse to dict for storing
    if hasattr(response, "data"):  # If DRF Response
        data = response.data
    else:  # If JsonResponse
        data = json.loads(response.content)

    # Update or create cache
    APICache.objects.update_or_create(
        request_hash=req_hash,
        defaults={
            "response": data,
            "expires_at": timezone.now() + TTL
        }
    )

def stream_json_in_chunks(data, chunk_size=4):
    keys = list(data.keys())
    total_keys = len(keys)

    for i in range(0, total_keys, chunk_size):
        chunk_keys = keys[i:i+chunk_size]
        chunk_data = {k: data[k] for k in chunk_keys}
        yield json.dumps({
            "keys": chunk_data,
            "chunk_index": (i // chunk_size) + 1,
            "total_chunks": (total_keys // chunk_size) + (1 if total_keys % chunk_size > 0 else 0)
        }) + "\n"

def get_component_api(request, slum_id):
    """
    Wrapper view with stale-while-revalidate caching
    """
    req_hash = get_request_hash(request, slum_id)

    try:
        cache = APICache.objects.get(request_hash=req_hash)
        data = cache.response
        # If cache is expired, start background refresh
        if cache.is_expired():
            
            # Start background refresh
            threading.Thread(target=compute_and_update_cache, args=(request, slum_id, req_hash)).start()

    except APICache.DoesNotExist:
        # No cache → compute synchronously
        response = views.get_component(request, slum_id)
        if hasattr(response, "data"):
            data = response.data
        else:
            data = json.loads(response.content)

        APICache.objects.create(
            request_hash=req_hash,
            response=data,
            expires_at=timezone.now() + TTL
        )

    return StreamingHttpResponse(stream_json_in_chunks(data, chunk_size=4), content_type="application/json")