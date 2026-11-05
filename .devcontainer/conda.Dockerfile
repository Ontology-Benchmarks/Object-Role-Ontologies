FROM continuumio/miniconda3:latest

# Update conda and install pip
RUN conda update -n base -c defaults conda -y \
    && conda install -y pip \
    && conda clean -afy

WORKDIR /workspace

EXPOSE 8888