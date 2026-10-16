export async function onRequest({ request, env }) {
  if (!env.BE_API_ORIGIN) {
    return new Response('BE_API_ORIGIN is not configured', { status: 500 })
  }

  const target = new URL(request.url)
  const backend = new URL(env.BE_API_ORIGIN)
  target.protocol = backend.protocol
  target.host = backend.host

  return fetch(new Request(target, request), { redirect: 'manual' })
}
