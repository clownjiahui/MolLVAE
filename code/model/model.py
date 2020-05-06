import torch
import torch.nn as nn
import torch.nn.functional as F
from code.model.decoders.LSTM_decoder import LSTM_decoder
from code.model.encoders.LSTM_encoder import LSTM_encoder


class LVAE(torch.nn.Module):
    def __init__(self,encoder_param,decoder_param,d_size,z_size,z2z_layer_size,batch_size):
        '''
        :param encoder_param: encoder type and encoder param
        :param decoder_param: decoder type and decoder param
        :param d_size: list contain d_size of LVAE. e.g. [512,256,128,64,32]
        :param z_size: list contain z_size of  LVAE e.g. [64,32,16,8,4]
        :param z2z_layer_size: list contain z2z layer size of LVAE e.g. [8,16,32,64]
        :param batch_size: batch size
        '''
        super(LVAE,self).__init__()
        self.d_size = d_size
        self.z_size = z_size
        self.z2z_layer_size = z2z_layer_size
        self.batch_size = batch_size
        self.z_reverse_size = list(reversed(z_size))
        self.encoder_param = encoder_param
        self.decoder_param = decoder_param
        if encoder_param.type == 'lstm':
            self.encoder = LSTM_encoder(encoder_param.param['embed_size'],encoder_param.param['hidden_size'],
                                        encoder_param.param['num_layers'],encoder_param.param['vocab'],batch_size,
                                        encoder_param.param['sorted_seq'],encoder_param.param['bidirectional'])
            self.LVAE_input_size = encoder_param.param['hidden_size'] * (1 + int(encoder_param.param['bidirectional']))
        else :
            raise ValueError(
                "Invalid encoder_type"
            )

        if decoder_param.type == 'lstm':
            self.decoder = LSTM_decoder(decoder_param.param['vocab'],decoder_param.param['embed_size'],decoder_param.param['latent_size'],
                                        decoder_param.param['deocder_hidden_size'],decoder_param.param['decoder_num_layers'],decoder_param.param['dropout'])
        else:
            raise ValueError(
                "Invalid decoder_type"
            )
        self.top_down_layers = []
        self.bottom_up_layers = []
        for i in range(len(self.d_size)):
            if i == 0:
                self.bottom_up_layers.append(MLP(in_size=self.LVAE_input_size,layer_size=self.d_size[i],out_size=self.z_size[i]))
            else:
                self.bottom_up_layers.append(MLP(in_size=self.d_size[i-1],layer_size=self.d_size[i],out_size=self.z_size[i]))
        for i in range(len(self.z_reverse_size)-1):
            self.top_down_layers.append(MLP(in_size=self.z_reverse_size[i],layer_size=self.z2z_layer_size[i],out_size=self.z_reverse_size[i+1]))

    def bottom_up(self,input):
        z_mu_q_d = []
        z_var_q_d = []
        for i in range(len(self.d_size)):
            if i == 0:
                nn, mu_q_d, var_q_d = self.bottom_up_layers[i](input)
                z_mu_q_d.append(mu_q_d)
                z_var_q_d.append(var_q_d)
            else:
                nn, mu_q_d, var_q_d = self.bottom_up_layers[i](nn)
                z_mu_q_d.append(mu_q_d)
                z_var_q_d.append(var_q_d)
        return z_mu_q_d, z_var_q_d

    def top_down(self, z_sample, z_mu_p, z_var_p):
        for i in range(len(self.z_reverse_size) - 1):
            _, mu_p, var_p = self.top_down_layers[i](z_sample[i])
            z_sample.append(self.sample_z(mu_p, var_p))
            z_mu_p.append(mu_p)
            z_var_p.append(var_p)
        return list(reversed(z_mu_p)), list(reversed(z_var_p)), list(reversed(z_sample))



    def sample_z(self, mu, var):
        stddev = var ** 0.5
        out = mu + stddev * torch.randn(mu.size())
        return out

    def Gaussian_up_date(self, mu_d, var_d, mu_up, var_up):
        x = torch.pow(var_d, -1)
        y = torch.pow(var_up, -1)
        sigma = torch.pow(torch.add(x, y), -1)
        var = torch.pow(sigma, 2)
        mu = torch.add(mu_d*x, mu_up*y) * sigma
        return mu, var

    def KL_loss(self,q_mu,q_var,p_mu,p_var):
        kl = 0.5*(torch.log(p_var)-torch.log(q_var) + q_var/p_var + torch.pow(torch.add(q_mu,-p_mu),2)/p_var -1)
        return kl.sum(1).mean()

    def forward_encoder(self,seq,lengths):
        _,input = self.encoder(seq,lengths)
        z_mu_p = []
        z_var_p = []
        z_sample = []
        z_mu_q = []
        z_var_q = []
        kl_loss = 0
        z_mu_q_d, z_var_q_d = self.bottom_up(input)
        z_mu_p.append(torch.zeros(z_mu_q_d[-1].size()))
        z_var_p.append(torch.ones(z_var_q_d[-1].size()))
        z_sample.append(self.sample_z(z_mu_q_d[-1], z_var_q_d[-1]))
        z_mu_p, z_var_p, z_sample = self.top_down(z_sample, z_mu_p, z_var_p)
        for i in range(len(self.z_size)):
            mu, var = self.Gaussian_up_date(z_mu_q_d[i], z_var_q_d[i], z_mu_p[i], z_var_p[i])
            z_mu_q.append(mu)
            z_var_q.append(var)
        for i in range(len(self.z_size)):
            kl_loss = kl_loss + self.KL_loss(z_mu_q[i], z_var_q[i], z_mu_p[i], z_var_p[i])
        return z_sample[0],kl_loss


    def forward(self, batch):
        seq,lengths = batch
        z,KL_loss= self.forward_encoder(seq,lengths)
        recon_loss = self.decoder(batch,z)
        return KL_loss,recon_loss

class MLP(torch.nn.Module):
    def __init__(self,in_size,layer_size,out_size):
        super(MLP,self).__init__()
        self.layer1 = nn.Linear(in_size,layer_size)
        self.layer2 = nn.Linear(layer_size,layer_size)
        self.mu = nn.Linear(layer_size,out_size)
        self.var = nn.Linear(layer_size,out_size)

    def forward(self,input):
        layer1 = F.leaky_relu(self.layer1(input))
        layer2 = F.leaky_relu(self.layer2(layer1))
        mu = self.mu(layer2)
        var = F.softplus(self.var(layer2)) + 1e-8
        return layer2,mu,var

