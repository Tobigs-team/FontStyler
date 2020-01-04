from src.models.model import AE_base
from src.data.common.dataset import FontDataset, PickledImageProvider

import torch
from torch.nn import functional as F
from torch.optim import SGD, Adam
from torch.utils.data import DataLoader
from torch.utils.data.sampler import SubsetRandomSampler

from ignite.engine import Events, Engine
from ignite.metrics import Loss, MeanSquaredError, RunningAverage

import numpy as np
from tqdm import tqdm

from matplotlib import pyplot as plt

if __name__ == '__main__':
    
    '''
    Configuration: 
    TODO - parse.args 활용
    '''
    batch_size = 32
    validation_split = .15
    test_split = .05
    shuffle_dataset = True
    random_seed = 42
    
    lr = 0.0002
    
    log_interval = 10
    epochs = 30
    
    device = 'cuda:1' if torch.cuda.is_available() else 'cpu'
    
    
    '''
    Dataset Loaders
    '''
    
    # get Dataset
    data_dir = 'src/data/dataset/allfonts/'
    train_set = FontDataset(PickledImageProvider(data_dir+'train.obj'))
    valid_set = FontDataset(PickledImageProvider(data_dir+'val.obj'))
    test_set = FontDataset(PickledImageProvider(data_dir+'test.obj'))
    
    # get idx samplers
    train_set_size = len(train_set)
    valid_set_size = len(valid_set)
    train_idxs = list(range(train_set_size))
    valid_idxs = list(range(valid_set_size))
    if shuffle_dataset:
        np.random.seed(random_seed)
        np.random.shuffle(train_idxs)
        np.random.shuffle(valid_idxs)
    
    train_sampler = SubsetRandomSampler(train_idxs)
    valid_sampler = SubsetRandomSampler(valid_idxs)
        
    # get data_loaders
    train_loader = DataLoader(train_set, 
                          batch_size=batch_size,
                          sampler=train_sampler
                          )
    valid_loader = DataLoader(valid_set,
                            batch_size=batch_size,
                            sampler=valid_sampler
                            )
    test_loader = DataLoader(test_set,
                            batch_size=len(test_set)
                            )
    
    '''
    Modeling
    '''
    model = AE_base(category_size=5,
                    alpha_size=52,
                    font_size=128*128, 
                    z_size=32)
    
    '''
    Optimizer
    TODO - 옵티마이저도 모델 안으로 넣기
    Abstract model 만들기?
    '''
    optimizer = Adam(model.parameters(), lr=lr)
    '''
    엔진 구축
    '''
    
    # Training 시 process_function
    def train_process(engine, batch):
        model.float().to(device).train()
        optimizer.zero_grad()
        vectors, font, _  = batch
        alpha_vector = vectors['alphabet_vector']
        category_vector = vectors['category_vector']
        
        font, alpha_vector = font.float().to(device), alpha_vector.float().to(device)
        category_vector = category_vector.float().to(device)
        
        font_hat, _ = model(font, alpha_vector, category_vector)
        
        loss = F.mse_loss(font_hat, font)
        loss.backward()
        
        optimizer.step()
        
        return loss.item()
    
    # Evaluating 시 process_function
    def evaluate_process(engine, batch):
        model.float().to(device).eval()
        with torch.no_grad():
            vectors, font, _ = batch
            alpha_vector = vectors['alphabet_vector']
            category_vector = vectors['category_vector']
            
            font, alpha_vector = font.float().to(device), alpha_vector.float().to(device)
            category_vector = category_vector.float().to(device)
            
            font_hat, _ = model(font, alpha_vector, category_vector)
            
            return font, font_hat
        
        
    trainer = Engine(train_process)
    evaluator = Engine(evaluate_process)
    
    
    RunningAverage(output_transform=lambda x: x).attach(trainer, 'mse')
    
    Loss(F.mse_loss, output_transform=lambda x: [x[1], x[0]]).attach(evaluator, 'mse')
    
    desc = "ITERATION - loss: {:.5f}"
    pbar = tqdm(
        initial=0, leave=False, total=len(train_loader),
        desc=desc.format(0)
    )

    train_history = []
    valid_history = []
    
    @trainer.on(Events.ITERATION_COMPLETED)
    def log_training_loss(engine):
        iter = (engine.state.iteration - 1) % len(train_loader) + 1
        
        if iter % log_interval == 0:
            pbar.desc = desc.format(engine.state.output)
            pbar.update(log_interval)
    @trainer.on(Events.EPOCH_COMPLETED)
    def log_training_results(engine):
        pbar.refresh()
        evaluator.run(train_loader)
        metrics = evaluator.state.metrics
        mse_loss = metrics['mse']
        # kld_loss = metrics['kld']
        tqdm.write(
            "Training Result - Epoch: {} MSE: {:.7f}"
            .format(engine.state.epoch, mse_loss)
        )
        global train_history
        train_history += [metrics['mse']]
    @trainer.on(Events.EPOCH_COMPLETED)
    def log_validation_results(engine):
        evaluator.run(valid_loader)
        metrics = evaluator.state.metrics
        mse_loss = metrics['mse']
        # kld_loss = metrics['kld']
        tqdm.write(
            "Validation Results - Epoch: {} MSE: {:.7f}"
            .format(engine.state.epoch, mse_loss)
        )
        global valid_history
        valid_history += [metrics['mse']]
        
    @trainer.on(Events.COMPLETED)
    def plot_history_results(engine):
        train_epoch = len(train_history)
        valid_epoch = len(valid_history)
        plt.plot(list(range(1, train_epoch+1)), train_history, label='train_history')
        plt.plot(list(range(1, valid_epoch+1)), valid_history, label='valid_history')
        plt.legend()
        plt.savefig('history_epoch_{}_3cat.png'.format(train_epoch))
        plt.close()
        
    @trainer.on(Events.COMPLETED)
    def plot_font_results(engine):
        evaluator.run(test_loader)
        real_font, fake_font = evaluator.state.output
        plt.figure(figsize=(50, 250))
        for i, (real, fake) in enumerate(zip(real_font[:131*24], fake_font[:131*24])):
            plt.subplot(131, 24, 2*i+1)
            plt.imshow(real.cpu().detach().numpy())
            plt.subplot(131, 24, 2*i+2)
            plt.imshow(fake.cpu().detach().numpy())
        plt.savefig('real_fake_fonts_{}_3cat.png'.format(engine.state.epoch))
        plt.close()
    
    model_path = 'AE_base_lr_{}_epochs_{}.pth'.format(lr, epochs)
    @trainer.on(Events.COMPLETED)
    def save_model(engine):
        torch.save(model.state_dict(), model_path)
        
        
    trainer.run(train_loader, max_epochs=epochs)
