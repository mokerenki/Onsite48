import { useEffect, useState } from 'react'

export function Footer() {
  const [year, setYear] = useState(new Date().getFullYear())

  useEffect(() => {
    setYear(new Date().getFullYear())
  }, [])

  return (
    <footer className="footer" role="contentinfo">
      <div className="container">
        <p>&copy; {year} {{PROJECT_NAME}}. All rights reserved.</p>
      </div>
    </footer>
  )
}