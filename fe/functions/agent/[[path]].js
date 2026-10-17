export async function onRequest({ request, env }) {
  if (!env.AGENT_API_ORIGIN) {
    return new Response('AGENT_API_ORIGIN is not configured', { status: 500 })
  }

  const target = new URL(request.url)
  const agent = new URL(env.AGENT_API_ORIGIN)
  target.protocol = agent.protocol
  target.host = agent.host

  const upstream = await fetch(new Request(target, request), { redirect: 'manual' })
  const headers = new Headers(upstream.headers)
  headers.set('Cache-Control', 'no-store')
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers,
  })
}
