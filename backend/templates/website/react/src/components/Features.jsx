const features = [
  {
    icon: '⚡',
    title: 'Lightning Fast',
    description: 'Built with Vite for instant HMR and optimized production builds.',
  },
  {
    icon: '🎨',
    title: 'Modern Design',
    description: 'Clean, responsive design with CSS custom properties for easy theming.',
  },
  {
    icon: '♿',
    title: 'Accessible',
    description: 'WCAG 2.1 AA compliant with semantic HTML and proper ARIA attributes.',
  },
  {
    icon: '📱',
    title: 'Responsive',
    description: 'Mobile-first approach that works beautifully on all device sizes.',
  },
  {
    icon: '🔧',
    title: 'Developer Friendly',
    description: 'ESLint, Prettier, and TypeScript-ready for a great DX.',
  },
  {
    icon: '🚀',
    title: 'Production Ready',
    description: 'Optimized builds with code splitting and asset optimization.',
  },
]

export function Features() {
  return (
    <section id="features" className="features" aria-labelledby="features-title">
      <div className="container">
        <h2 id="features-title">Features</h2>
        <div className="features-grid">
          {features.map((feature, index) => (
            <article key={index} className="feature-card">
              <div className="feature-icon" aria-hidden="true">
                {feature.icon}
              </div>
              <h3>{feature.title}</h3>
              <p>{feature.description}</p>
            </article>
          ))}
        </div>
      </div>
    </section>
  )
}