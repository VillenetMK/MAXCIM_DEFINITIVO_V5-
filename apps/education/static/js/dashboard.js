document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".profile__avatar-img").forEach((img) => {
    const remove = () => img.remove();
    img.addEventListener("error", remove);
    if (img.complete && img.naturalWidth === 0) remove();
  });

  const sidebar = document.getElementById("sidebar");
  const sidebarToggle = document.getElementById("sidebarToggle");
  const profileTrigger = document.getElementById("profileTrigger");
  const profileMenu = document.getElementById("profileMenu");

  sidebarToggle.addEventListener("click", () => {
    if (window.matchMedia("(max-width: 760px)").matches) {
      sidebar.classList.toggle("is-open");
    } else {
      sidebar.classList.toggle("is-collapsed");
    }
  });

  profileTrigger.addEventListener("click", (event) => {
    event.stopPropagation();
    profileMenu.classList.toggle("is-open");
  });

  document.addEventListener("click", (event) => {
    if (!profileMenu.contains(event.target) && !profileTrigger.contains(event.target)) {
      profileMenu.classList.remove("is-open");
    }
    if (
      window.matchMedia("(max-width: 760px)").matches
      && sidebar.classList.contains("is-open")
      && !sidebar.contains(event.target)
      && !sidebarToggle.contains(event.target)
    ) {
      sidebar.classList.remove("is-open");
    }
  });
});
