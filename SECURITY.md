# Security

Please report security problems privately through GitHub: the repository's **Security** tab,
**Report a vulnerability**. Do not open a public issue for them.

What the app touches: the scanner's two USB cameras (video and standard camera settings, plus one
read of the serial number, the same request Creality's app sends), files inside its own folder, and
on first run the Python Package Index (pip) to install numpy and Pillow. It opens no network ports.

This is a work-in-progress hobby project with no warranty; reports are handled on a best-effort basis.
