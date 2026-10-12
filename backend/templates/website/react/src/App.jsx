import { Header } from './components/Header'
import { Hero } from './components/Hero'
import { Features } from './components/Features'
import { About } from './components/About'
import { Contact } from './components/Contact'
import { Footer } from './components/Footer'

function App() {
  return (
    <>
      <Header />
      <main>
        <Hero />
        <Features />
        <About />
        <Contact />
      </main>
      <Footer />
    </>
  )
}

export default App