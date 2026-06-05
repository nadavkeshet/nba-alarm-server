// Cloudflare Pages Function: webcal://clutchalarm.com/cal/{token}.ics
// Proxies the dynamic ICS feed from the Railway backend under the branded domain.
export async function onRequestGet(context) {
  const { token } = context.params;            // e.g. "abcd1234.ics" (includes .ics)
  const base = context.env.RAILWAY_BASE
            || "https://nba-alarm-server-dev.up.railway.app";

  // token already carries the .ics suffix; backend route is /calendar/{token}.ics
  const upstream = await fetch(`${base}/calendar/${token}`, {
    headers: { Accept: "text/calendar" },
    cf: { cacheTtl: 300, cacheEverything: true },
  });

  if (upstream.status === 404) {
    return new Response("Calendar not found", { status: 404 });
  }
  if (!upstream.ok) {
    return new Response("Upstream error", { status: 502 });
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "text/calendar; charset=utf-8",
      "Content-Disposition": 'inline; filename="clutch-alarm.ics"',
      "Cache-Control": "public, max-age=300",
      "Access-Control-Allow-Origin": "*",
    },
  });
}
