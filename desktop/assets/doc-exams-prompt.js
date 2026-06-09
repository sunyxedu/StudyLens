new Promise((resolve) => {
  const form = document.getElementById("form");
  const cancel = document.getElementById("cancel");
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    resolve({
      username: document.getElementById("username").value,
      password: document.getElementById("password").value
    });
  });
  cancel.addEventListener("click", () => resolve(null));
})
