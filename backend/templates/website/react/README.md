# {{PROJECT_NAME}}

{{PROJECT_DESCRIPTION}}

A modern React + Vite website template with:
- ⚡ Vite for fast development and optimized builds
- 🎨 CSS Custom Properties for easy theming
- ♿ WCAG 2.1 AA accessible
- 📱 Mobile-first responsive design
- 🔧 ESLint + React hooks linting
- 📦 Production-ready build configuration

## Quick Start

```bash
# Install dependencies
npm install

# Start development server
npm run dev

# Build for production
npm run build

# Preview production build
npm run preview

# Lint code
npm run lint
```

## Project Structure

```
├── public/                 # Static assets
│   └── vite.svg           # Favicon
├── src/
│   ├── components/        # React components
│   │   ├── Header.jsx
│   │   ├── Hero.jsx
│   │   ├── Features.jsx
│   │   ├── About.jsx
│   │   ├── Contact.jsx
│   │   └── Footer.jsx
│   ├── App.jsx            # Root component
│   ├── main.jsx           # Entry point
│   └── index.css          # Global styles
├── index.html             # HTML template
├── vite.config.js         # Vite configuration
├── package.json
└── README.md
```

## Customization

### Colors
Edit CSS custom properties in `src/index.css`:

```css
:root {
  --color-primary: #2563eb;
  --color-primary-dark: #1d4ed8;
  --color-primary-light: #dbeafe;
  --color-secondary: #0891b2;
  --color-secondary-dark: #0e7490;
}
```

### Content
Update the placeholder values in components:
- `{{PROJECT_NAME}}` - Your project name
- `{{HERO_TITLE}}` - Main headline
- `{{HERO_SUBTITLE}}` - Supporting text
- `{{CTA_PRIMARY}}` - Primary CTA text
- `{{CTA_SECONDARY}}` - Secondary CTA text
- `{{ABOUT_TEXT}}` - About section content

### Adding Features
Edit `src/components/Features.jsx` to add/modify features.

## Deployment

### Static Hosting (Netlify, Vercel, GitHub Pages, etc.)
```bash
npm run build
# Deploy the `dist` folder
```

### Docker
```dockerfile
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
```

## Scripts

| Command | Description |
|---------|-------------|
| `npm run dev` | Start dev server with HMR |
| `npm run build` | Production build to `dist/` |
| `npm run preview` | Preview production build locally |
| `npm run lint` | Run ESLint on source files |

## Browser Support

- Chrome (latest)
- Firefox (latest)
- Safari (latest)
- Edge (latest)

## License

MIT License