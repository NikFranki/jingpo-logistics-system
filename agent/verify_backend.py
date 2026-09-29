"""Optional read-only BE contract smoke check. No model, keys or .env access."""
import argparse
import asyncio
import json

from be_client import (get_order, get_shipment, get_stations, get_transport_task,
                       list_orders, list_shipments, list_transport_tasks)
from state import TurnContext


async def verify(base_url):
    context = TurnContext()
    results = []
    for label, listing, detail in [
        ('orders', list_orders, get_order),
        ('shipments', list_shipments, get_shipment),
        ('transport_tasks', list_transport_tasks, get_transport_task),
    ]:
        response = await listing(base_url, context, page_size=1)
        results.append({'check': label + '_list', 'status': 'ok', 'request_id': response['request_id']})
        items = response['data']['items']
        if items:
            response = await detail(base_url, int(items[0]['id']), context)
            results.append({'check': label + '_detail', 'status': 'ok', 'request_id': response['request_id']})
        else:
            results.append({'check': label + '_detail', 'status': 'skipped_empty_list'})
    response = await get_stations(base_url, context)
    results.append({'check': 'stations', 'status': 'ok', 'request_id': response['request_id']})
    print(json.dumps({'checks': results, 'http_calls': context.http_calls}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    asyncio.run(verify(parser.parse_args().base_url))
