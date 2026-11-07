// Small enhancements for a well page: copy coordinates to the clipboard.
for (const button of document.querySelectorAll('button.copy')) {
  button.addEventListener('click', async () => {
    const text = button.dataset.copy || '';
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = 'Copied';
    } catch {
      button.textContent = text;
    }
    setTimeout(() => { button.textContent = 'Copy coordinates'; }, 1800);
  });
}
