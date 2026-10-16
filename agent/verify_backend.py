"""Optional read-only BE contract smoke check. Uses the CLI personal login from the OS credential store; no model calls."""
import argparse
import asyncio
import json

from be_client import (get_order, get_shipment, get_stations, get_transport_task,
                       get_shipment_schedule, list_shipment_destination_changes,
                       list_shipment_schedule_history, list_orders, list_shipments,
                       list_transport_tasks)
from state import TurnContext


async def verify(base_url, identity, auth):
    context = TurnContext(identity=identity, identity_client=auth)
    results = []
    operations = [
        ('orders', list_orders, get_order),
        ('shipments', list_shipments, get_shipment),
        ('transport_tasks', list_transport_tasks, get_transport_task),
    ]
    if identity.own_only:
        operations = [entry for entry in operations if entry[0] != "transport_tasks"]
    for label, listing, detail in operations:
        response = await listing(base_url, context, page_size=1)
        results.append({'check': label + '_list', 'status': 'ok', 'request_id': response['request_id']})
        items = response['data']['items']
        if items:
            response = await detail(base_url, int(items[0]['id']), context)
            results.append({'check': label + '_detail', 'status': 'ok', 'request_id': response['request_id']})
            if label == 'shipments':
                shipment_id = int(items[0]['id'])
                for check, operation in (
                    ('shipment_schedule', lambda: get_shipment_schedule(base_url, shipment_id, context)),
                    ('shipment_schedule_history', lambda: list_shipment_schedule_history(base_url, shipment_id, context, page_size=1)),
                    ('shipment_destination_changes', lambda: list_shipment_destination_changes(base_url, shipment_id, context, page_size=1)),
                ):
                    checked = await operation()
                    results.append({'check': check, 'status': 'ok', 'request_id': checked['request_id']})
        else:
            results.append({'check': label + '_detail', 'status': 'skipped_empty_list'})
            if label == 'shipments':
                results.extend({'check': name, 'status': 'skipped_empty_list'} for name in (
                    'shipment_schedule', 'shipment_schedule_history', 'shipment_destination_changes',
                ))
    response = await get_stations(base_url, context)
    results.append({'check': 'stations', 'status': 'ok', 'request_id': response['request_id']})
    print(json.dumps({'checks': results, 'http_calls': context.http_calls}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default=None)
    args = parser.parse_args()
    async def main():
        from cli_client import CLIClient
        from identity import IdentityError
        client = CLIClient.configured()
        if args.base_url:
            client = CLIClient(args.base_url, client.agent_url)
        if not await client.resume():
            print("请先用 python cli.py 的 /login 登录，并保存到系统凭据库。")
            raise SystemExit(1)
        identity = await client.auth.authenticate(client.token)
        try:
            await verify(client.be_url, identity, client.auth)
        except IdentityError as exc:
            print(exc.message)
            raise SystemExit(1)
    asyncio.run(main())
