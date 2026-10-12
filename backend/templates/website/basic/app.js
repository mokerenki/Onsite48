// {{PROJECT_NAME}} - Basic Website JavaScript

(function() {
  'use strict';

  // Current year for footer
  const yearElement = document.querySelector('.footer p');
  if (yearElement) {
    yearElement.textContent = yearElement.textContent.replace('{{CURRENT_YEAR}}', new Date().getFullYear());
  }

  // Smooth scroll for anchor links
  document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', function(e) {
      const targetId = this.getAttribute('href');
      if (targetId === '#') return;
      const target = document.querySelector(targetId);
      if (target) {
        e.preventDefault();
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        target.focus({ preventScroll: true });
      }
    });
  });

  // Contact form handling (demo)
  const contactForm = document.querySelector('.contact-form');
  if (contactForm) {
    contactForm.addEventListener('submit', function(e) {
      e.preventDefault();
      const formData = new FormData(this);
      const data = Object.fromEntries(formData.entries());
      console.log('Form submitted:', data);
      alert('Thank you for your message! This is a demo form.');
      this.reset();
    });
  }

  // Header scroll effect
  const header = document.querySelector('.header');
  if (header) {
    let lastScrollY = window.scrollY;
    window.addEventListener('scroll', () => {
      const currentScrollY = window.scrollY;
      if (currentScrollY > 100) {
        header.style.boxShadow = 'var(--shadow-md)';
      } else {
        header.style.boxShadow = 'none';
      }
      lastScrollY = currentScrollY;
    }, { passive: true });
  }

  // Intersection Observer for fade-in animations
  const observerOptions = {
    root: null,
    rootMargin: '0px 0px -50px 0px',
    threshold: 0.1
  };

  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('visible');
        observer.unobserve(entry.target);
      }
    });
  }, observerOptions);

  document.querySelectorAll('.feature-card, .about-content, .contact-form').forEach(el => {
    el.style.opacity = '0';
    el.style.transform = 'translateY(20px)';
    el.style.transition = 'opacity 0.6s ease, transform 0.6s ease';
    observer.observe(el);
  });

  // Add visible class styles dynamically
  const style = document.createElement('style');
  style.textContent = `
    .visible {
      opacity: 1 !important;
      transform: translateY(0) !important;
    }
  `;
  document.head.appendChild(style);

  // Keyboard navigation enhancement
  document.addEventListener('keydown', (e) => {
    // Skip links activation
    if (e.key === 'Tab' && !document.querySelector('.skip-link:focus')) {
      const skipLink = document.querySelector('.skip-link');
      if (skipLink) skipLink.focus();
    }
  });

  console.log('{{PROJECT_NAME}} initialized');
})();