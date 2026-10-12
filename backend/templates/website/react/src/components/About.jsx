export function About() {
  return (
    <section id="about" className="about" aria-labelledby="about-title">
      <div className="container">
        <h2 id="about-title">About</h2>
        <div className="about-content">
          <p>{{ABOUT_TEXT}}</p>
        </div>
      </div>
    </section>
  )
}