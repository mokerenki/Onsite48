/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  outputFileTracingRoot: __dirname,
  
  async rewrites() {
    // `backend` is resolvable only on the Docker Compose network. When a
    // developer runs `npm run dev` directly on the host, the API lives on
    // localhost instead; using the Docker hostname there makes API requests
    // fail and leaves the browser on a loading screen.
    const backendUrl = process.env.BACKEND_URL || (
      process.env.DOCKER_ENV === 'true' ? 'http://backend:8000' : 'http://localhost:8000'
    );
    console.log(`[Next.js] Proxying API requests to: ${backendUrl}`);
    
    return [
      {
        source: '/api/:path*',
        destination: `${backendUrl}/api/:path*`,
      },
      {
        source: '/ws/:path*',
        destination: `${backendUrl}/ws/:path*`,
      },
    ];
  },
  
  // WebSocket upgrade support for dev server proxy
  async headers() {
    return [
      {
        source: '/ws/:path*',
        headers: [
          { key: 'Connection', value: 'Upgrade' },
          { key: 'Upgrade', value: 'websocket' },
        ],
      },
    ];
  },
};

module.exports = nextConfig;