import math
import sys

sys.path.append("./")

import os, argparse, glob, datetime, yaml
import torch
from torch.utils.data import DataLoader
import time
import numpy as np
from tqdm import tqdm, trange
import joblib

from omegaconf import OmegaConf
from PIL import Image

from lidm.models.diffusion.ddim import DDIMSampler
from lidm.utils.misc_utils import instantiate_from_config, set_seed, isimage
from lidm.utils.lidar_utils import range2pcd
from lidm.modules.encoders.modules import (
    FrozenCLIPTextEmbedder,
    FrozenClipMultiTextEmbedder,
)

# remove annoying user warnings
import warnings

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)

DATASET2METRICS = {"kitti": ["frid", "fsvd", "fpvd"], "nuscenes": ["fsvd", "fpvd"]}

custom_to_range = lambda x: (x * 255.0).clamp(0, 255).floor() / 255.0


def custom_to_pcd(x, config, rgb=None):
    x = x.squeeze().detach().cpu().numpy()
    x = (np.clip(x, -1.0, 1.0) + 1.0) / 2.0
    if rgb is not None:
        rgb = rgb.squeeze().detach().cpu().numpy()
        rgb = (np.clip(rgb, -1.0, 1.0) + 1.0) / 2.0
        rgb = rgb.transpose(1, 2, 0)
    xyz, rgb, _ = range2pcd(x, color=rgb, **config["data"]["params"]["dataset"])

    return xyz, rgb


def custom_to_pil(x):
    x = x.detach().cpu().squeeze().numpy()
    x = (np.clip(x, -1.0, 1.0) + 1.0) / 2.0
    x = (255 * x).astype(np.uint8)

    if x.ndim == 3:
        x = x.transpose(1, 2, 0)
    x = Image.fromarray(x)

    return x


def custom_to_np(x):
    x = x.detach().cpu().squeeze().numpy()
    x = (np.clip(x, -1.0, 1.0) + 1.0) / 2.0
    x = x.astype(
        np.float32
    )  # NOTE: use predicted continuous depth instead of np.uint8 depth
    return x


def logs2pil(logs, keys=["sample"]):
    imgs = dict()
    for k in logs:
        try:
            if len(logs[k].shape) == 4:
                img = custom_to_pil(logs[k][0, ...])
            elif len(logs[k].shape) == 3:
                img = custom_to_pil(logs[k])
            else:
                print(f"Unknown format for key {k}. ")
                img = None
        except:
            img = None
        imgs[k] = img
    return imgs


@torch.no_grad()
def convsample(
    model, cond, shape, return_intermediates=True, verbose=True, make_prog_row=False
):
    if not make_prog_row:
        return model.p_sample_loop(
            cond, shape, return_intermediates=return_intermediates, verbose=verbose
        )
    else:
        return model.progressive_denoising(cond, shape, verbose=verbose)


@torch.no_grad()
def convsample_ddim(model, cond, steps, shape, eta=1.0, verbose=False):
    ddim = DDIMSampler(model)
    bs = shape[0]
    shape = shape[1:]
    samples, intermediates = ddim.sample(
        steps,
        conditioning=cond,
        batch_size=bs,
        shape=shape,
        eta=eta,
        verbose=verbose,
        disable_tqdm=True,
    )
    return samples, intermediates


@torch.no_grad()
def make_convolutional_sample(
    model, cond, batch_size, vanilla=False, custom_steps=None, eta=1.0, verbose=False
):
    log = dict()
    shape = [
        batch_size,
        model.model.diffusion_model.in_channels,
        *model.model.diffusion_model.image_size,
    ]

    with model.ema_scope("Plotting"):
        t0 = time.time()
        if vanilla:
            sample, progrow = convsample(
                model, cond, shape, make_prog_row=True, verbose=verbose
            )
        else:
            sample, intermediates = convsample_ddim(
                model, cond, custom_steps, shape, eta, verbose
            )
        t1 = time.time()
    x_sample = model.decode_first_stage(sample)

    log["sample"] = x_sample
    log["time"] = t1 - t0
    log["throughput"] = sample.shape[0] / (t1 - t0)
    if verbose:
        print(f'Throughput for this batch: {log["throughput"]}')
    return log


def run(
    model,
    text_encoder,
    prompt,
    imglogdir,
    pcdlogdir,
    custom_steps=50,
    batch_size=10,
    n_samples=50,
    config=None,
    verbose=False,
):
    tstart = time.time()
    n_saved = len(glob.glob(os.path.join(imglogdir, "*.png")))

    all_samples = []
    print(f"Running conditional sampling")
    for _ in trange(
        math.ceil(n_samples / batch_size), desc="Sampling Batches (unconditional)"
    ):
        with torch.no_grad():
            cond = text_encoder.encode(batch_size * [prompt])
            cond = model.cond_stage_model(cond)
        try:
            logs = make_convolutional_sample(
                model, cond, batch_size, custom_steps=custom_steps, verbose=verbose
            )
        except Exception:
            import pdb as debugger

            debugger.post_mortem()
        n_saved = save_logs(
            logs, imglogdir, pcdlogdir, n_saved=n_saved, key="sample", config=config
        )

    print(
        f"Sampling of {n_saved} images finished in {(time.time() - tstart) / 60.:.2f} minutes."
    )
    return all_samples


def save_logs(
    logs, imglogdir, pcdlogdir, n_saved=0, key="sample", np_path=None, config=None
):
    batch = logs[key]
    if np_path is None:
        for x in batch:
            # save as image
            img = custom_to_pil(x)
            imgpath = os.path.join(imglogdir, f"{key}_{n_saved:06}.png")
            img.save(imgpath)
            # save as point cloud
            xyz, rgb = custom_to_pcd(x, config)
            pcdpath = os.path.join(pcdlogdir, f"{key}_{n_saved:06}.txt")
            np.savetxt(pcdpath, np.hstack([xyz, rgb]), fmt="%.6f")
            n_saved += 1
    return n_saved


def get_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-r",
        "--resume",
        type=str,
        nargs="?",
        help="load from logdir or checkpoint in logdir",
        default="none",
    )
    parser.add_argument(
        "-p",
        "--prompt",
        type=str,
        nargs="?",
        default="walls surrounded",
        help="the prompt to render",
    )
    parser.add_argument(
        "-n",
        "--n_samples",
        type=int,
        nargs="?",
        help="number of samples to draw",
        default=50,
    )
    parser.add_argument(
        "-e",
        "--eta",
        type=float,
        nargs="?",
        help="eta for ddim sampling (0.0 yields deterministic sampling)",
        default=1.0,
    )
    parser.add_argument(
        "--vanilla",
        default=False,
        action="store_true",
        help="vanilla sampling (default option is DDIM sampling)?",
    )
    parser.add_argument(
        "-l", "--logdir", type=str, nargs="?", help="extra logdir", default="none"
    )
    parser.add_argument(
        "-c",
        "--custom_steps",
        type=int,
        nargs="?",
        help="number of steps for ddim and fastdpm sampling",
        default=50,
    )
    parser.add_argument(
        "-b", "--batch_size", type=int, nargs="?", help="the bs", default=10
    )
    parser.add_argument(
        "--num_views", type=int, nargs="?", help="num of views", default=4
    )
    parser.add_argument(
        "--apply_all",
        default=False,
        action="store_true",
        help="print status?",
    )
    parser.add_argument(
        "-s", "--seed", type=int, help="the numpy file path", default=1000
    )
    parser.add_argument(
        "-d",
        "--dataset",
        type=str,
        help="dataset name [nuscenes, kitti]",
        required=True,
    )
    parser.add_argument(
        "-v",
        "--verbose",
        default=False,
        action="store_true",
        help="print status?",
    )
    return parser


def load_model_from_config(config, sd):
    model = instantiate_from_config(config)
    model.load_state_dict(sd, strict=False)
    model.cuda()
    model.eval()
    return model


def load_model(config, ckpt):
    if ckpt:
        print(f"Loading model from {ckpt}")
        pl_sd = torch.load(ckpt, map_location="cpu")
        global_step = pl_sd["global_step"]
    else:
        pl_sd = {"state_dict": None}
        global_step = None
    model = load_model_from_config(config.model, pl_sd["state_dict"])
    return model, global_step


def build_text_encoder(num_views, apply_all):
    model = FrozenClipMultiTextEmbedder(num_views=num_views, apply_all=apply_all)
    model.freeze()
    return model


def visualize(samples, logdir):
    pcdlogdir = os.path.join(logdir, "pcd")
    os.makedirs(pcdlogdir, exist_ok=True)
    for i, pcd in enumerate(samples):
        # save as point cloud
        pcdpath = os.path.join(pcdlogdir, f"{i:06}.txt")
        np.savetxt(pcdpath, pcd, fmt="%.3f")


def test_collate_fn(data):
    output = {}
    keys = data[0].keys()
    for k in keys:
        v = [d[k] for d in data]
        if k not in ["reproj"]:
            v = torch.from_numpy(np.stack(v, 0))
        else:
            v = [d[k] for d in data]
        output[k] = v
    return output


if __name__ == "__main__":
    now = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    sys.path.append(os.getcwd())
    command = " ".join(sys.argv)

    parser = get_parser()
    opt, unknown = parser.parse_known_args()
    ckpt = None
    set_seed(opt.seed)

    if not os.path.exists(opt.resume) and not os.path.exists(opt.file):
        raise ValueError("Cannot find {}".format(opt.resume))
    if os.path.isfile(opt.resume):
        try:
            logdir = "/".join(opt.resume.split("/")[:-1])
            print(f"Logdir is {logdir}")
        except ValueError:
            paths = opt.resume.split("/")
            idx = -2  # take a guess: path/to/logdir/checkpoints/model.ckpt
            logdir = "/".join(paths[:idx])
        ckpt = opt.resume
    elif os.path.isfile(opt.file):
        try:
            logdir = "/".join(opt.file.split("/")[:-5])
            if len(logdir) == 0:
                logdir = "/".join(opt.file.split("/")[:-1])
            print(f"Logdir is {logdir}")
        except ValueError:
            paths = opt.resume.split("/")
            idx = -5  # take a guess: path/to/logdir/samples/step_num/date/numpy/*.npz
            logdir = "/".join(paths[:idx])
        ckpt = None
    else:
        assert os.path.isdir(opt.resume), f"{opt.resume} is not a directory"
        logdir = opt.resume.rstrip("/")
        ckpt = os.path.join(logdir, "model.ckpt")

    base_configs = [f"{logdir}/config.yaml"]
    opt.base = base_configs

    configs = [OmegaConf.load(cfg) for cfg in opt.base]
    cli = OmegaConf.from_dotlist(unknown)
    config = OmegaConf.merge(*configs, cli)

    gpu = True
    eval_mode = True
    if opt.logdir != "none":
        locallog = logdir.split(os.sep)[-1]
        if locallog == "":
            locallog = logdir.split(os.sep)[-2]
        print(
            f"Switching logdir from '{logdir}' to '{os.path.join(opt.logdir, locallog)}'"
        )
        logdir = os.path.join(opt.logdir, locallog)

    print(config)

    model, global_step = load_model(config, ckpt)
    print(f"global step: {global_step}")
    print(75 * "=")
    print("logging to:")
    logdir = os.path.join(
        logdir, "samples", f"{global_step:08}", opt.prompt.replace(" ", "_")
    )
    imglogdir = os.path.join(logdir, "img")
    pcdlogdir = os.path.join(logdir, "pcd")
    numpylogdir = os.path.join(logdir, "numpy")

    os.makedirs(imglogdir, exist_ok=True)
    os.makedirs(pcdlogdir, exist_ok=True)
    os.makedirs(numpylogdir, exist_ok=True)
    print(logdir)
    print(75 * "=")

    # write config out
    sampling_file = os.path.join(logdir, "sampling_config.yaml")
    sampling_conf = vars(opt)

    with open(sampling_file, "w") as f:
        yaml.dump(sampling_conf, f, default_flow_style=False)
    print(sampling_conf)

    text_encoder = build_text_encoder(opt.num_views, opt.apply_all)
    run(
        model,
        text_encoder,
        opt.prompt,
        imglogdir,
        pcdlogdir,
        custom_steps=opt.custom_steps,
        config=config,
        verbose=opt.verbose,
    )
