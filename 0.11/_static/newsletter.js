// Submit the Buttondown signup in the background so the reader stays on the
// docs page instead of being sent to buttondown.com. Falls back to a normal
// form POST if this script never runs.
document.addEventListener("DOMContentLoaded", function () {
  var form = document.querySelector(".embeddable-buttondown-form");
  if (!form) return;

  var status = document.getElementById("bd-status");
  var button = form.querySelector('input[type="submit"]');

  function setStatus(message, kind) {
    if (!status) return;
    status.textContent = message;
    status.className = "bd-status" + (kind ? " " + kind : "");
    status.hidden = false;
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (button) button.disabled = true;
    setStatus("Subscribing…", "");

    // URLSearchParams keeps this a simple CORS request (no preflight).
    fetch(form.action, {
      method: "POST",
      body: new URLSearchParams(new FormData(form)),
    })
      .then(function (response) {
        if (response.ok) {
          form.reset();
          setStatus(
            "Thanks! Check your inbox to confirm your subscription.",
            "success"
          );
        } else {
          setStatus(
            "That didn’t work — check the address and try again.",
            "error"
          );
          if (button) button.disabled = false;
        }
      })
      .catch(function () {
        setStatus("Network error — please try again.", "error");
        if (button) button.disabled = false;
      });
  });
});
