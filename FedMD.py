import torch
import torch.optim as optim
import torch.nn as nn
import numpy as np
import copy
from model_trainers import *
from CIFAR import stratified_sampling
import wandb
from constants import *

class FedMD():
    # parties changed to agents
    # N_alignment changed to N_subset
    
    def __init__(self, agents, model_saved_names, public_dataset, 
                 private_data, total_private_data,  
                 private_test_data, N_subset,
                 N_rounds, 
                 N_logits_matching_round, logits_matching_batchsize, 
                 N_private_training_round, private_training_batchsize):

        self.N_agents = len(agents)
        self.model_saved_names = model_saved_names
        self.public_dataset = public_dataset
        self.private_data = private_data
        self.private_test_data = private_test_data
        self.N_subset = N_subset
        
        self.N_rounds = N_rounds
        self.N_logits_matching_round = N_logits_matching_round
        self.logits_matching_batchsize = logits_matching_batchsize
        self.N_private_training_round = N_private_training_round
        self.private_training_batchsize = private_training_batchsize
        
        self.collaborative_agents = []
        self.init_result = []

        print("start model initialization: ")
        for i in range(self.N_agents):
            print("model ", i)
            model_A = copy.deepcopy(agents[i]) # Was clone_model
            # model_A.set_weights(agents[i].get_weights())
            model_A.load_state_dict(agents[i].state_dict())
            # model_A.compile(optimizer=tf.keras.optimizers.Adam(lr = LR), 
            #                      loss = "sparse_categorical_crossentropy",
            #                      metrics = ["accuracy"])
            optimizer = optim.Adam(model_A.parameters(), lr = LR, weight_decay=WEIGHT_DECAY)
            loss = nn.CrossEntropyLoss()
            
            print("start full stack training ... ")        
            
            # model_A.fit(private_data[i]["X"], private_data[i]["y"],
            #                  batch_size = 32, epochs = 25, shuffle=True, verbose = 0,
            #                  validation_data = [private_test_data["X"], private_test_data["y"]],
            #                  callbacks=[EarlyStopping(monitor='val_accuracy', min_delta=0.001, patience=10)]
            #                 )
            # OBS: Also passes the validation data and uses EarlyStopping
            # TODO: Early stopping on train_model

            accuracy = train_model(model_A, private_data[i], loss, batch_size=32, num_epochs=25, optimizer=optimizer, returnAcc=True)
            best_test_acc = max(accuracy, key=lambda x: x["test_accuracy"])["test_accuracy"]
            wandb.run.summary[f"{model_saved_names[i]}_initial_test_acc"] = best_test_acc
            
            print("full stack training done")
            
            # model_A = remove_last_layer(model_A, loss="mean_absolute_error")
            # model_A = nn.Sequential(*(list(model_A.children())[:-1])) # Removing last layer of the model_A
            
            self.collaborative_agents.append({"model_logits": model_A, 
                                               "model_classifier": model_A,
                                               "model_weights": model_A.state_dict()}) # Was get_weights()
            
            # TODO: Need to include also the validation dataset on model_train and save these statistics
            # self.init_result.append({"val_acc": model_A.history.history['val_accuracy'],
            #                          "train_acc": model_A.history.history['accuracy'],
            #                          "val_loss": model_A.history.history['val_loss'],
            #                          "train_loss": model_A.history.history['loss'],
            #                         })
            
            print()
            del model_A
        #END FOR LOOP
        
        print("calculate the theoretical upper bounds for participants: ")
        
        self.upper_bounds = []
        self.pooled_train_result = []
        for model in agents:
            model_ub = copy.deepcopy(model)
            # model_ub.set_weights(model.get_weights())
            model_ub.load_state_dict(model.state_dict())
            # model_ub.compile(optimizer=tf.keras.optimizers.Adam(lr = LR),
            #                  loss = "sparse_categorical_crossentropy", 
            #                  metrics = ["accuracy"])
            optimizer = optim.Adam(model_ub.parameters(), lr = LR, weight_decay=WEIGHT_DECAY)
            loss = nn.CrossEntropyLoss()
            
            # model_ub.fit(total_private_data["X"], total_private_data["y"],
            #              batch_size = 32, epochs = 50, shuffle=True, verbose = 0, 
            #              validation_data = [private_test_data["X"], private_test_data["y"]],
            #              callbacks=[EarlyStopping(monitor="val_accuracy", min_delta=0.001, patience=10)])
            # OBS: Validation accuracy == our test accuracy since it is the value at the end of each epoch

            accuracy = train_model(model_ub, total_private_data, loss, batch_size=32, num_epochs=50, optimizer=optimizer, returnAcc=True)[-1] 
            best_test_acc = max(accuracy, key=lambda x: x["test_accuracy"])
            wandb.run.summary[f"{model_saved_names[i]}_ub_test_acc"] = best_test_acc["test_accuracy"]
            wandb.run.summary[f"{model_saved_names[i]}_ub_train_acc"] = best_test_acc["train_accuracy"]
            

            # self.upper_bounds.append(model_ub.history.history["val_accuracy"][-1])
            self.upper_bounds.append(accuracy["test_accuracy"]) ## SHOULD BE VAL ACCURACY!
            # self.pooled_train_result.append({"val_acc": model_ub.history.history["val_accuracy"], 
            #                                  "acc": model_ub.history.history["accuracy"]}) # "accuracy" == train accuracy
            self.pooled_train_result.append({"test_acc": accuracy["test_accuracy"], 
                                             "acc": accuracy["train_accuracy"]})
            
            del model_ub    
        print("the upper bounds are:", self.upper_bounds)
    # end init

    def collaborative_training(self):
        # start collaborating training    
        collaboration_performance = {i: [] for i in range(self.N_agents)}
        r = 0
        while True:
            # At beginning of each round, generate new alignment dataset
            # alignment_data = generate_alignment_data(self.public_dataset["X"], 
            #                                          self.public_dataset["y"],
            #                                          self.N_subset)
            alignment_data = stratified_sampling(self.public_dataset, self.N_subset)
            
            print("round ", r)
            
            print("update logits ... ")
            # update logits
            logits = 0
            for agent in self.collaborative_agents:
                # agent["model_logits"].set_weights(agent["model_weights"])
                agent["model_logits"].load_state_dict(agent["model_weights"])
                # logits += agent["model_logits"].predict(alignment_data["X"], verbose = 0) 
                model_logits = run_dataset(agent["model_logits"], alignment_data)
                model_logits = torch.max(model_logits, 1) 
                logits += model_logits
                
            logits /= self.N_agents
            
            # test performance
            print("test performance ... ")
            
            for index, agent in enumerate(self.collaborative_agents):
                # y_pred = agent["model_classifier"].predict(self.private_test_data["X"], verbose = 0).argmax(axis = 1)
                accuracy = test_network(network=agent, test_dataset=alignment_data)
                
                print(f"Model {index} got accuracy of {accuracy}")
                wandb.log({f"{self.model_saved_names[index]}_test_acc": accuracy*100}, step=r)
                collaboration_performance[index].append(accuracy)              
                
            r += 1
            if r > self.N_rounds:
                break
                
                
            print("updates models ...")
            for index, agent in enumerate(self.collaborative_agents):
                print("model {0} starting alignment with public logits... ".format(index))
                
                weights_to_use = None
                weights_to_use = agent["model_weights"]

                # agent["model_logits"].set_weights(weights_to_use)
                agent["model_logits"].load_state_dict(weights_to_use)
                # agent["model_logits"].fit(alignment_data["X"], logits, 
                #                       batch_size = self.logits_matching_batchsize,  
                #                       epochs = self.N_logits_matching_round, 
                #                       shuffle=True, verbose = 0)
                optimizer = optim.Adam(agent["model_logits"].parameters(), lr = LR, weight_decay=WEIGHT_DECAY)
                loss = nn.CrossEntropyLoss()
                alignment_data.targets = logits
                train_model(agent["model_logits"], alignment_data, loss, 
                    batch_size=self.logits_matching_batchsize, 
                    num_epochs=self.N_logits_matching_round, 
                    optimizer=optimizer)


                # agent["model_weights"] = agent["model_logits"].get_weights()
                agent["model_weights"] = agent["model_logits"].state_dict()

                print("model {0} done alignment".format(index))

                print("model {0} starting training with private data... ".format(index))
                weights_to_use = None
                weights_to_use = agent["model_weights"]

                # agent["model_classifier"].set_weights(weights_to_use)
                agent["model_classifier"].load_state_dict(weights_to_use)

                # agent["model_classifier"].fit(self.private_data[index]["X"], 
                #                           self.private_data[index]["y"],       
                #                           batch_size = self.private_training_batchsize, 
                #                           epochs = self.N_private_training_round, 
                #                           shuffle=True, verbose = 0)

                optimizer = optim.Adam(agent["model_classifier"].parameters(), lr = LR, weight_decay=WEIGHT_DECAY)
                loss = nn.CrossEntropyLoss()
                train_model(agent["model_classifier"], self.private_data[index], 
                    loss, 
                    batch_size=self.private_training_batchsize, 
                    num_epochs=self.N_private_training_round,
                    optimizer=optimizer)

                # agent["model_weights"] = agent["model_classifier"].get_weights()
                agent["model_weights"] = agent["model_classifier"].state_dict()
                
                print("model {0} done private training. \n".format(index))
            #END FOR LOOP
        
        #END WHILE LOOP
        return collaboration_performance
        