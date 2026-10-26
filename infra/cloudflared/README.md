The tunnel is configured in the Cloudflare dashboard (remote-managed): one public hostname,
`astro.ayandev.app`, routed to `http://caddy:80`. The container only needs the tunnel token, which is
stored SOPS-encrypted as `cloudflared_token` in `secrets/<env>.enc.yaml`. No inbound ports are opened on
the host. Enable "Always Use HTTPS", HSTS and WebSockets for the zone.
