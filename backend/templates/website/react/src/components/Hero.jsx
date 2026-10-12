export function Hero() {
  return (
    <section className="hero" aria-labelledby="hero-title">
      <div className="container">
        <h1 id="hero-title">{{HERO_TITLE}}</h1>
        <p className="hero-subtitle">{{HERO_SUBTITLE}}</p>
        <div className="hero-actions">
          <a href="#contact" className="btn btn-primary">
            {{CTA_PRIMARY}}
          </a>
          <a href="#features" className="btn btn-secondary">
            {{CTA_SECONDARY}}
          </a>
        </div>
      </div>
    </section>
  )
}