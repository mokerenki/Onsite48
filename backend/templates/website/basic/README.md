# {{PROJECT_NAME}}

{{PROJECT_DESCRIPTION}}

## Features

{{#FEATURES}}
- **{{TITLE}}**: {{DESCRIPTION}}
{{/FEATURES}}

## Quick Start

### Development
```bash
# No build step required for the basic template
# Simply open index.html in your browser, or use a local server:
npx serve .
# or
python3 -m http.server 8000
```

### Production
Deploy the entire project folder to any static hosting:
- Netlify
- Vercel
- GitHub Pages
- Cloudflare Pages
- AWS S3 + CloudFront
- Any web server

## Project Structure

```
├── index.html          # Main HTML entry point
├── styles.css          # All styles (CSS custom properties for theming)
├── app.js              # Client-side JavaScript
└── README.md           # This file
```

## Customization

### Colors
Edit the CSS custom properties in `styles.css`:

```css
:root {
  --color-primary: #2563eb;      /* Primary brand color */
  --color-primary-dark: #1d4ed8; /* Darker shade for hover */
  --color-primary-light: #dbeafe; /* Lighter shade for backgrounds */
  --color-secondary: #0891b2;    /* Secondary color */
  --color-secondary-dark: #0e7490;
}
```

### Content
Update the placeholder values in `index.html`:
- `{{PROJECT_NAME}}` - Your project name
- `{{PROJECT_DESCRIPTION}}` - Brief description
- `{{HERO_TITLE}}` - Main headline
- `{{HERO_SUBTITLE}}` - Supporting text
- `{{CTA_PRIMARY}}` - Primary call-to-action text
- `{{CTA_SECONDARY}}` - Secondary call-to-action text
- Features section (repeat as needed)
- `{{ABOUT_TEXT}}` - About section content

### Fonts
The template uses Inter from Google Fonts. To change:
1. Update the `<link>` in `index.html`
2. Update `--font-family` in `styles.css`

## Accessibility

This template follows WCAG 2.1 AA guidelines:
- Semantic HTML5 elements
- Proper heading hierarchy
- ARIA labels where needed
- Focus visible states
- Color contrast ratios
- Keyboard navigable
- Reduced motion support

## Browser Support

- Chrome (latest)
- Firefox (latest)
- Safari (latest)
- Edge (latest)

## License

MIT License - feel free to use for any project.