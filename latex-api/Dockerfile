
# Targeted TeX Live install via apt, instead of the texlive/texlive:latest
# scheme-full image. The full image is 4-7GB+ and does not fit on a server
# with ~4GB free disk. This installs only what the CV template actually
# needs: xelatex (for fontspec/system fonts) plus the specific CTAN
# packages it uses, which is closer to 1-1.5GB total.
FROM ubuntu:22.04
 
ENV DEBIAN_FRONTEND=noninteractive
 
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        texlive-base \
        texlive-latex-base \
        texlive-latex-recommended \
        texlive-latex-extra \
        texlive-fonts-recommended \
        texlive-xetex \
        latexmk \
        fonts-liberation \
        fontconfig \
        python3 \
        python3-pip \
        curl \
    && fc-cache -f \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
 
WORKDIR /app
 
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
 
COPY main.py .
 
EXPOSE 8000
 
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
 