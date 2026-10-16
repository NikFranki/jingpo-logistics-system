"""Compact terminal CLI sessions using public checkpointer APIs only."""
from copy import deepcopy


async def compact_session(graph, config):
    # Called only after a turn has finished or recovery has completed.
    snapshot = await graph.aget_state(config)
    if snapshot.next:
        raise RuntimeError('cannot compact a running graph')
    values = deepcopy(snapshot.values)
    await graph.checkpointer.adelete_thread(config['configurable']['thread_id'])
    if values:
        await graph.aupdate_state(config, values, as_node='stop')


async def delete_session(graph, thread_id):
    await graph.checkpointer.adelete_thread(thread_id)
