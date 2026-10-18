"""Dummy fixtures. Real data arrives from the GUI later; nothing here reads I/O.

Shaped like the template this design came from — two degrees at one school, graduate
coursework, a leadership section — so rendering SAMPLE exercises every branch.
"""

from generate_resume import Course, Education, Experience, Leadership, Project, Resume

SAMPLE = Resume(
    name="Dana Okonkwo",
    location="3375 Country Glen Trl, Frisco, TX 75034",
    email="dana.okonkwo@example.com",
    phone="214-469-5227",
    links=(("linkedin.com/in/dokonkwo", "https://linkedin.com/in/dokonkwo"),
           ("github.com/dokonkwo", "https://github.com/dokonkwo")),
    summary="Electrical engineer, happiest where analog design and firmware argue.",
    education=(
        Education(
            school="Purdue University", location="West Lafayette, IN",
            degree="Intended Master of Science in Electrical and Computer Engineering",
            concentration="Concentration in VLSI and Circuit Design",
            end="2028-12", tags=("vlsi", "circuit design"),
            coursework=(Course("CMOS Analog IC Design", graduate=True),
                        Course("Nanoelectronics Fundamentals", graduate=True),
                        Course("Digital Signal Processing", graduate=True),
                        Course("Signals and Systems"),
                        Course("Microprocessor Systems"),
                        Course("Digital System Design"),
                        Course("Advanced C Programming"),
                        Course("Probabilistic Methods"))),
        Education(
            school="Purdue University",
            degree="Bachelor of Science in Electrical Engineering",
            concentration="Concentration in Microelectronics and Semiconductors",
            gpa="3.09", start="2023-08", end="2027-05",
            tags=("microelectronics", "semiconductors")),
    ),
    skills=(("Languages",
             "Verilog-A/AMS, SystemVerilog, Tcl, C/C++, MATLAB, Python, Perl, ARM64 Assembly"),
            ("Tools",
             "Synopsys IC Design Suite, Cadence Virtuoso, Spectre, LTSpice, Altium, KiCAD"),
            ("Technologies/Frameworks", "UVM, PyVISA, PyLTSpice, Embedded Systems, Linux, Git")),
    experience=(
        Experience(
            company="Ixana", title="Hardware Engineering Intern — Startup",
            location="West Lafayette, IN", start="2026-05", end="2026-08",
            tags=("rf", "python", "test automation", "instrumentation"),
            bullets=(
                "Led RF characterization and validation on Wi-R technology, using near-field "
                "electric-field measurements to evaluate TX/RX performance, RSSI, and packet "
                "error rate under highly varied conditions.",
                "Developed a Python test framework and GUI to control spectrum-analysis "
                "instrumentation, enabling repeatable acquisition, processing, and "
                "visualization pipelines.",
                "Designed a long-term failure-monitoring architecture for an adaptive system "
                "feature, enabling real-time detection of fixture failures and data corruption.",
            )),
        Experience(
            company="Texas Instruments",
            title="Product Marketing Engineering Intern — BLDC Motor Drivers",
            location="Dallas, TX", start="2025-05", end="2025-08",
            tags=("motor drivers", "product marketing", "gate drive"),
            bullets=(
                "Developed product briefs for 10+ brushless motor drivers, detailing "
                "gate-drive architecture, footprint, and competitive positioning for field "
                "applications engineers.",
                "Guided 6+ field teams targeting the 2/3-wheeler traction inverter market, "
                "synthesizing systems-engineer feedback on customer board modifications.",
            )),
        Experience(
            company="Center for Secure Microelectronics Ecosystem",
            title="Undergraduate Researcher — MRAM",
            location="West Lafayette, IN", start="2026-05",
            tags=("mram", "systemverilog", "fpga", "reliability"),
            bullets=(
                "Executed accelerated-aging characterization of a commercial 4-Mb STT-MRAM at "
                "110°C, driving 1.05M read-write-read cycles and ~52.4B stress writes "
                "through an FPGA-based SystemVerilog testbench.",
                "Developed Python scripts to parse 1M+ verification cycles of RWR data, "
                "calculating BER, cumulative errors, and reliability metrics.",
            )),
    ),
    projects=(
        Project(
            name="Celestial Object Tracking System (COTS)",
            url="https://github.com/example/COTS", start="2024-05",
            stack=("PCB Design", "Signal Integrity Analysis", "Embedded Systems"),
            tags=("pcb", "stm32", "embedded", "spi", "uart"),
            bullets=(
                "Led hardware development for a custom 4-layer PCB bridging a NUCLEO-G474RE "
                "microcontroller, stepper drivers, and a BLE module, iterating routing, "
                "impedance control, and return paths.",
                "Architected UART and SPI communication, tested motor interactions, and "
                "refined sensor filtering for optimal responsiveness.",
            )),
        Project(
            name="Optical Heartbeat Sensor", start="2026-04", end="2026-05",
            stack=("Analog Design", "Signal Processing", "Mixed-Signal Systems"),
            tags=("analog", "filters", "comparator"),
            bullets=(
                "Built an optical heartbeat sensing system with an IR emitter/receiver pair and "
                "a multi-stage analog front end amplifying a ~10 mV signal with cascaded "
                "active HPF/LPF stages (0.67–3.33 Hz, ~576× gain).",
                "Designed an LM339 comparator-based ADC with adjustable hysteresis, resilient "
                "to noise and baseline drift across varying ambient lighting.",
            )),
    ),
    leadership=(
        Leadership(
            org="Semiconductor Student Alliance — Purdue Chapter", role="Treasurer",
            start="2025-05", tags=("leadership", "budget"),
            bullets=(
                "Managed chapter budget and funding allocations supporting 11+ industry and "
                "social events.",
                "Organized 6+ workshops and info sessions with leading semiconductor companies.",
            )),
        Leadership(
            org="Star City Campus Ministry", role="President", start="2026-08",
            bullets=("Organized and coordinated events for the campus community.",)),
    ),
)

POSTING = """Analog IC Design Engineer. You will own CMOS analog blocks in Cadence
Virtuoso and Spectre, characterize silicon on the bench with PyVISA-driven
instrumentation, and work with embedded firmware teams on mixed-signal validation."""

if __name__ == "__main__":
    import os

    import generate_resume as gr

    os.makedirs("out", exist_ok=True)
    path = os.path.join("out", "resume.tex")
    with open(path, "w", encoding="utf-8") as f:
        f.write(gr.render(SAMPLE))
    print("tex  ->", path)

    if gr.engine():
        print("pdf  ->", gr.to_pdf(SAMPLE, "out/resume.pdf"))
        trimmed = gr.tailor(SAMPLE, POSTING, keep=2)
        print("tail ->", gr.to_pdf(trimmed, "out/resume_tailored.pdf"))
    else:
        print("pdf  -> skipped:", gr.NoEngine())
