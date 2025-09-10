from django.test import RequestFactory
from django.contrib.auth.models import AnonymousUser
from component import views
import time

def run_test(slum_ids=None):
    if slum_ids is None:
        slum_ids = [1971]

    factory = RequestFactory()

    for slum_id in slum_ids:
        request = factory.get(f'/component/get_component/{slum_id}')
        request.user = AnonymousUser()  # <-- fix here

        start = time.perf_counter()
        response = views.get_component(request, slum_id)
        end = time.perf_counter()

        print(f"Slum ID {slum_id} - Time taken: {end - start:.4f} seconds")
        print("Response length:", len(response.content))
        print("-" * 50)
