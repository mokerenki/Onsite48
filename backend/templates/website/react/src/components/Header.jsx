import { useState, useEffect } from 'react'

export function Header() {
  const [scrolled, setScrolled] = useState(false)

  useEffect(() => {
    const handleScroll = () => setScrolled(window.scrollY > 20)
    window.addEventListener('scroll', handleScroll, { passive: true })
    return () => window.removeEventListener('scroll', handleScroll)
  }, [])

  const navLinks = [
    { href: '#features', label: 'Features' },
    { href: '#about', label: 'About' },
    { href: '#contact', label: 'Contact' },
  ]

  return (
    <header
      className="header"
      style={{
        boxShadow: scrolled ? 'var(--shadow-md)' : 'none',
      }}
      role="banner"
    >
      <div className="container">
        <nav className="nav" aria-label="Main navigation">
          <a href="/" className="logo" aria-label="{{PROJECT_NAME}} home">
            {{PROJECT_NAME}}
          </a>
          <ul className="nav-links">
            {navLinks.map((link) => (
              <li key={link.href}>
                <a href={link.href}>{link.label}</a>
              </li>
            ))}
          </ul>
        </nav>
      </div>
    </header>
  )
}