from astro_worker.tasks import render


def test_confirmation_email_uses_site_time() -> None:
    subject, body = render(
        "booking_confirmed",
        {
            "id": "b1",
            "start": "2026-10-29T15:00:00+00:00",
            "end": "2026-10-29T17:00:00+00:00",
            "telescope": "Optical Refractor",
            "target": "Saturn",
        },
        "Kit",
    )
    assert subject == "Booked: Saturn on the Optical Refractor"
    assert "Hi Kit" in body and "8 PM to 10 PM" in body and "Thursday 29 October" in body
