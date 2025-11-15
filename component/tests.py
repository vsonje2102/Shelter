import time
from django.test import RequestFactory
from component import views

def run_test(slum_ids=None):
    """
    Measure execution time of get_component for given slum IDs.

    :param slum_ids: list of slum IDs to test, default [1971]
    """
    if slum_ids is None:
        slum_ids = [1971]

    factory = RequestFactory()

    for slum_id in slum_ids:
        request = factory.get(f'/component/get_component/{slum_id}')
        
        start = time.perf_counter()
        response = views.get_component(request, slum_id)
        end = time.perf_counter()

        print(f"Slum ID {slum_id} - Time taken: {end - start:.4f} seconds")
        print("Response length:", len(response.content))
        print("-" * 50)
