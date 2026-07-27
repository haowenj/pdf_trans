document.addEventListener('DOMContentLoaded', () => {
  const article = document.querySelector('#reader-article');
  const printButton = document.querySelector('#print-button');
  renderMathInElement(article, {
    delimiters: [
      { left: '$$', right: '$$', display: true },
      { left: '$', right: '$', display: false },
      { left: '\\(', right: '\\)', display: false },
      { left: '\\[', right: '\\]', display: true }
    ],
    throwOnError: false,
    trust: false,
    maxSize: 20,
    maxExpand: 500
  });
  printButton.disabled = false;
  printButton.addEventListener('click', () => window.print());
});
