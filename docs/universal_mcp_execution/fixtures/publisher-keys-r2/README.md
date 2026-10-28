# Publisher Keys r2 fixture

This unbound template preserves the publisher strict field, hash and closed-publication checks. The Keys change handles an empty object without the prior strict-mode property access failure. The three binding literals remain null and do not grant runtime authority.

Run the standalone pure-logic `fixture.ps1` with PowerShell to exercise the old and revised Keys behavior; success prints `FIXTURE_OK`. This fixture does not perform target IO, SDK or native operations. The original runtime failure remains a failure record.
